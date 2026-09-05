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

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from sure.runtime.model.bootstrap import manifest_sha256, materialize_runtime
from sure.site.loader import load_site_policy


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
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
    output = Path(args.produces).resolve() if args.produces else artifacts / "docker_registry_result.json"
    write_json(output, payload)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
