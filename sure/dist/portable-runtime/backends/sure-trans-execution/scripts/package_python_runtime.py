#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / "sure" / "runtime").is_dir()),
    Path(__file__).resolve().parents[4],
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sure.runtime.model.bootstrap import manifest_sha256, materialize_runtime
from sure.site.loader import load_site_policy
from sure.runtime.execution_bridge import (
    artifact_ref,
    build_receipt,
    build_request,
    capability_evidence,
    digest_json,
    snapshot_digest,
    write_contract_bundle,
)


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


_CONTRACT_CONTEXT: dict | None = None


def _start_runtime_contract(run_dir: Path, output: Path, artifacts: Path, resolved: dict) -> None:
    global _CONTRACT_CONTEXT
    input_paths = [
        path
        for path in (
            artifacts / "trans_input_resolved.json",
            Path(str(resolved.get("lockfile") or "")),
            artifacts / "adapter_manifest.json",
        )
        if path.is_file()
    ]
    snapshot = snapshot_digest(input_paths)
    inputs = [
        artifact_ref(path, origin="local_staging", source_root=run_dir, reference_snapshot_digest=snapshot, artifact_id=f"input-{index}")
        for index, path in enumerate(input_paths, start=1)
    ]
    requirements = [
        {"capability_id": "sure.execution.harness-python", "capability_class": "execution_capability", "required": True},
        {"capability_id": "sure.execution.uv", "capability_class": "execution_capability", "required": True},
    ]
    evidence = [
        capability_evidence("sure.execution.harness-python", status="AVAILABLE" if Path(sys.executable).is_file() else "MISSING", details={"executable": sys.executable}),
        capability_evidence("sure.execution.uv", status="AVAILABLE" if shutil.which("uv") else "MISSING", details={"executable": "uv"}),
    ]
    request = build_request(
        run_id=run_dir.name,
        unit_id="package_container",
        operation="package",
        entrypoint={"executable": sys.executable, "argv": [str(Path(__file__).resolve()), "--run-dir", str(run_dir)], "working_directory": str(run_dir)},
        output_root=run_dir,
        subject={
            "bundle_manifest_path": str(Path(str(resolved.get("model_dir") or run_dir)).expanduser().resolve()),
            "bundle_digest": resolved.get("model_payload_sha256") or digest_json(resolved),
            "runtime_identity_digest": resolved.get("python_executable") or digest_json({"backend": "uv"}),
            "dataset_identity_digest": snapshot,
        },
        inputs=inputs,
        capability_requirements=requirements,
        runtime_requirements={"backend": "uv", "compatibility_mode": "legacy_views"},
        reference_snapshot_digest=snapshot,
    )
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "execution_request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _CONTRACT_CONTEXT = {
        "run_dir": run_dir,
        "artifacts": artifacts,
        "request": request,
        "evidence": evidence,
        "output": output,
        "executor_kind": "python",
    }


def _finish_runtime_contract(*, lifecycle: str, exit_code: int | None, diagnostics: list[dict] | None = None) -> None:
    global _CONTRACT_CONTEXT
    context = _CONTRACT_CONTEXT
    if context is None:
        return
    outputs: list[dict] = []
    output = context.get("output")
    if isinstance(output, Path) and output.is_file():
        outputs.append(artifact_ref(output, origin="generated", source_root=context["run_dir"], artifact_id="output-1"))
    receipt = build_receipt(
        context["request"],
        lifecycle=lifecycle,
        executor_kind=context["executor_kind"],
        capability_evidence_values=context["evidence"],
        outputs=outputs,
        exit_code=exit_code if lifecycle != "NOT_STARTED" else None,
        diagnostics=diagnostics or [],
    )
    write_contract_bundle(context["artifacts"], context["request"], receipt, legacy_result=output if isinstance(output, Path) else None)
    _CONTRACT_CONTEXT = None


LOCAL_REQUIREMENT = re.compile(
    r"(?P<prefix>^\s*|\s+@\s+)(?P<path>(?:\.\.?[/\\])[^\s]+)",
    re.MULTILINE,
)


def promote_lockfile(source: Path, model_dir: Path) -> tuple[Path, list[str]]:
    """Copy a hash lock and its relative local distributions into the bundle."""
    text = source.read_text(encoding="utf-8")
    promoted: list[str] = []

    def replace(match: re.Match[str]) -> str:
        raw = match.group("path")
        local = (source.parent / Path(raw.replace("\\", "/"))).resolve()
        if not local.is_file() or local.is_symlink():
            raise ValueError(f"locked local distribution is missing or unsafe: {raw}")
        digest = sha256_file(local)
        destination_relative = Path("artifacts") / "local-distributions" / f"{digest[:16]}-{local.name}"
        destination = model_dir / destination_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and sha256_file(destination) != digest:
            raise ValueError(f"bundled local distribution has conflicting content: {destination_relative}")
        if not destination.exists():
            shutil.copy2(local, destination)
        portable = "./" + destination_relative.as_posix()
        promoted.append(portable)
        return match.group("prefix") + portable

    rewritten = LOCAL_REQUIREMENT.sub(replace, text)
    destination = model_dir / "requirements.lock"
    if destination.exists() and destination.read_text(encoding="utf-8") != rewritten:
        raise ValueError("model bundle requirements.lock already exists with different content")
    if not destination.exists():
        destination.write_text(rewritten, encoding="utf-8")
    return destination, sorted(set(promoted))


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    output = Path(args.produces).resolve() if args.produces else artifacts / "docker_registry_result.json"
    _start_runtime_contract(run_dir, output, artifacts, resolved)
    if resolved.get("source_kind") != "python" or resolved.get("package_profile") != "none":
        raise ValueError("package_python_runtime.py requires Python input with package=none")
    model_dir = Path(str(resolved["model_dir"])).resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    source_lock = Path(str(resolved["lockfile"])).resolve()
    promoted_lock, local_distributions = promote_lockfile(source_lock, model_dir)
    site = load_site_policy(required=True)
    assert site is not None
    execution = site["policy"]["execution"]
    if "local" not in execution["surfaces"] or "python" not in execution["local_runtimes"]:
        raise ValueError("site policy does not allow local Python runtimes")
    contract = materialize_runtime(
        runtime_root=Path(site["policy"]["storage"]["runtime_root"]) / "models",
        source_python=Path(str(resolved["python_executable"])),
        lock_path=promoted_lock,
    )
    manifest = {
        key: value
        for key, value in contract.items()
        if key not in {"runtime_root", "manifest_path", "python_executable_resolved", "manifest_sha256", "probe"}
    }
    write_json(artifacts / "model_runtime_manifest.json", manifest)
    write_json(model_dir / "artifacts" / "model_runtime_manifest.json", manifest)
    adapter = read_object(artifacts / "adapter_manifest.json")
    for key in ("model_py", "init_py", "validate_py", "server_py", "config_yaml", "model_spec"):
        source = Path(str(adapter.get(key) or "")).resolve()
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"adapter file is missing or unsafe: {key}")
        shutil.copy2(source, model_dir / source.name)
    config_path = model_dir / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("adapter config.yaml must be an object")
    server = config.get("server") if isinstance(config.get("server"), dict) else {}
    server["command"] = [manifest["python_executable"], "server.py"]
    server["working_dir"] = "."
    config["server"] = server
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    payload = {
        "schema": "sure.trans.python_package_result.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed",
        "package_profile": "none",
        "runtime_kind": "python",
        "backend": "uv",
        "lockfile_path": "requirements.lock",
        "lock_sha256": manifest["lock_sha256"],
        "local_distributions": local_distributions,
        "model_runtime": {
            "runtime_id": manifest["runtime_id"],
            "python_executable": manifest["python_executable"],
            "python_version": manifest["python_version"],
            "python_abi": manifest["python_abi"],
            "python_platform": manifest["python_platform"],
            "manifest_path": "artifacts/model_runtime_manifest.json",
            "manifest_sha256": manifest_sha256(manifest),
        },
        "server_command": [manifest["python_executable"], "server.py"],
        "working_dir": ".",
        "tool_names": [str(read_object(artifacts / "mcp_result.json").get("tool_name") or "predict")],
    }
    write_json(output, payload)
    print(output)
    return 0


def main() -> int:
    try:
        result = _main()
        _finish_runtime_contract(lifecycle="SUCCEEDED" if result == 0 else "FAILED", exit_code=0 if result == 0 else result)
        return result
    except Exception as error:
        missing = isinstance(error, (FileNotFoundError, OSError)) or "missing" in str(error).lower() or "uv" in str(error).lower()
        _finish_runtime_contract(
            lifecycle="NOT_STARTED" if missing else "FAILED",
            exit_code=None if missing else 1,
            diagnostics=[{"code": "CAPABILITY_MISSING" if missing else "EXECUTOR_FAILED", "message": str(error)}],
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
