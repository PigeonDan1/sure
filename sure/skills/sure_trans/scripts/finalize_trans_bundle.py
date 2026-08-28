#!/usr/bin/env python3
"""Finalize a portable SURE trans model bundle.

Seals the harness-owned `sure/models/<model_name>/` bundle with the same
product layout and terminal sidecars as /sure_onboard:
  model.spec.yaml, model.py, server.py, __init__.py, validate.py, config.yaml
  fixture/<task>/ (fixture + gt.jsonl)
  artifacts/{package_gate, runtime_inventory, verdict, docker_registry_result,
             artifact_manifest, deployment_ready}.json
The last written file is deployment_ready.json, written identically to the run
directory and the model bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
MODELS_ROOT = REPO_ROOT / "sure" / "models"

REQUIRED_ARTIFACTS = [
    "trans_input_resolved.json",
    "inference_dependency_report.json",
    "framework_detection.json",
    "fixture_manifest.json",
    "execution_compat.json",
    "original_inference_result.json",
    "source_image_result.json",
    "model_payload_manifest.json",
    "adapter_manifest.json",
    "adapter_image_result.json",
    "import_result.json",
    "load_result.json",
    "infer_result.json",
    "contract_result.json",
    "mcp_result.json",
    "equivalence_result.json",
    "docker_registry_result.json",
    "runtime_inventory.json",
    "verdict.json",
]

WRAPPER_FILES = ("model.py", "server.py", "__init__.py", "validate.py", "config.yaml", "model.spec.yaml", "Dockerfile.sure")

TERMINAL_FILES = (
    "package_gate.json",
    "runtime_inventory.json",
    "verdict.json",
    "docker_registry_result.json",
    "artifact_manifest.json",
    "deployment_ready.json",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_identical(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def resolve_model_dir(resolved: dict) -> Path:
    path_policy = resolved.get("path_policy") if isinstance(resolved.get("path_policy"), dict) else {}
    allowed_root = Path(str(path_policy.get("allowed_model_root") or MODELS_ROOT)).expanduser().resolve()
    model_dir = Path(str(resolved.get("model_dir") or "")).expanduser()
    try:
        model_dir = model_dir.resolve()
    except OSError:
        model_dir = model_dir.absolute()
    if model_dir.parent != allowed_root or model_dir.name != str(resolved["model_name"]):
        raise ValueError(
            f"model_dir must be the harness-owned bundle {allowed_root / str(resolved['model_name'])}; "
            f"refusing external destination {model_dir}"
        )
    return model_dir


def stage_wrapper(adapter_dir: Path, model_dir: Path) -> None:
    for name in WRAPPER_FILES:
        source = adapter_dir / name
        if not source.is_file():
            raise ValueError(f"adapter file missing: {source}")
        shutil.copy2(source, model_dir / name)


def stage_fixture(run_dir: Path, model_dir: Path, resolved: dict) -> None:
    fixture_manifest = read_object(run_dir / "artifacts" / "fixture_manifest.json")
    staged = Path(str(fixture_manifest.get("staged_path") or "")).expanduser()
    if not staged.is_file():
        raise ValueError(f"staged fixture is missing: {staged}")
    task = str(resolved.get("task_type") or "asr").lower()
    fixture_dir = model_dir / "fixture" / task
    fixture_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(staged, fixture_dir / staged.name)
    original = read_object(run_dir / "artifacts" / "original_inference_result.json")
    text = ""
    for key in ("output", "text", "transcript"):
        raw = original.get(key)
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            break
    gt = {"audio": staged.name, "task_type": task, "text": text}
    (fixture_dir / "gt.jsonl").write_text(json.dumps(gt, ensure_ascii=False) + "\n", encoding="utf-8")


def stage_artifacts(run_dir: Path, model_dir: Path) -> dict[str, str]:
    artifacts = run_dir / "artifacts"
    model_artifacts = model_dir / "artifacts"
    hashes: dict[str, str] = {}
    for name in REQUIRED_ARTIFACTS:
        source = artifacts / name
        if not source.is_file():
            raise ValueError(f"required artifact missing: {source}")
        shutil.copy2(source, model_artifacts / name)
    for name in ("validation.log", "sample_output.json"):
        source = artifacts / name
        if source.is_file():
            shutil.copy2(source, model_artifacts / name)
    return hashes


def write_package_gate(run_dir: Path, model_dir: Path, registry: dict) -> dict:
    adapter_image = read_object(run_dir / "artifacts" / "adapter_image_result.json")
    package = {
        "schema": "sure.onboard.package_gate.v2",
        "generated_at": now_iso(),
        "status": "passed",
        "package_profile": "docker-registry",
        "model_name": str(model_dir.name),
        "model_dir": ".",
        "artifact_manifest_path": "artifacts/artifact_manifest.json",
        "readiness": {
            "local_ready": True,
            "docker_ready": True,
            "registry_ready": registry.get("pull_verified") is True,
            "bundle_ready": True,
        },
        "docker": {
            "dockerfile_path": "Dockerfile.sure",
            "dockerfile_sha256": sha256(model_dir / "Dockerfile.sure"),
            "base_image": adapter_image.get("source_image"),
            "target_image": registry.get("target_image"),
            "target_image_digest": registry.get("target_image_digest"),
            "target_image_ref": registry.get("target_image_ref"),
            "build_result_path": "artifacts/adapter_image_result.json",
            "validation_result_path": "artifacts/contract_result.json",
            "registry_result_path": "artifacts/docker_registry_result.json",
        },
        "notes": "Container-only Eval binding produced by /sure_trans.",
    }
    content = json_bytes(package)
    write_identical(run_dir / "artifacts" / "package_gate.json", content)
    write_identical(model_dir / "artifacts" / "package_gate.json", content)
    return package


def write_artifact_manifest(run_dir: Path, model_dir: Path, resolved: dict) -> dict:
    required = {
        name.replace(".", "_").replace("-", "_"): {
            "path": f"artifacts/{name}",
            "description": f"Finalized trans artifact: {name}.",
        }
        for name in TERMINAL_FILES
    }
    optional = {
        name.replace(".", "_").replace("-", "_"): {
            "path": f"artifacts/{name}",
            "description": f"Transformation evidence: {name}.",
        }
        for name in REQUIRED_ARTIFACTS
        if name not in TERMINAL_FILES
    }
    manifest = {
        "schema": "sure.onboard.artifact_manifest.v1",
        "model_dir": ".",
        "model_id": resolved["model_name"],
        "model_name": resolved["model_name"],
        "phase": "deployment_ready",
        "status": "finalized",
        "generated_at": now_iso(),
        "timestamp": now_iso(),
        "artifacts": {"required": required, "conditional": {}, "optional": optional},
    }
    content = json_bytes(manifest)
    path = model_dir / "artifacts" / "artifact_manifest.json"
    write_identical(path, content)
    return manifest


def build_deployment_ready(run_dir: Path, model_dir: Path, resolved: dict, registry: dict, package: dict) -> dict:
    inventory = read_object(model_dir / "artifacts" / "runtime_inventory.json")
    verdict = read_object(model_dir / "artifacts" / "verdict.json")
    if verdict.get("status") not in {"passed", "success", "PASS", "PASSED", "pass"}:
        raise ValueError("verdict must be terminal-success before finalizing")
    if inventory.get("status") != "ready":
        raise ValueError("runtime inventory must be ready before finalizing")
    if registry.get("status") != "passed" or registry.get("pull_verified") is not True:
        raise ValueError("docker-registry bundle requires passed registry push and digest pull verification")

    required_names = [name for name in TERMINAL_FILES if name != "deployment_ready.json"]
    hashes = {
        f"artifacts/{name}": sha256(model_dir / "artifacts" / name)
        for name in required_names
    }
    bundle_identity = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    deployment = {
        "schema": "sure.onboard.deployment_ready.v1",
        "generated_at": now_iso(),
        "status": "ready",
        "model_name": str(resolved["model_name"]),
        "package_profile": str(package.get("package_profile") or "docker-registry"),
        "target_image": registry.get("target_image"),
        "target_image_digest": registry.get("target_image_digest"),
        "target_image_ref": registry.get("target_image_ref"),
        "runtime_inventory": "artifacts/runtime_inventory.json",
        "package_gate": "artifacts/package_gate.json",
        "verdict": "artifacts/verdict.json",
        "artifact_manifest": "artifacts/artifact_manifest.json",
        "required_artifact_sha256": hashes,
        "bundle_identity_sha256": bundle_identity,
        "execution_policy": {
            "container_only": True,
            "nfs_models_read_only": True,
            "host_python_fallback": False,
            "approved_image_override": False,
        },
    }
    harness = inventory.get("harness_runtime") if isinstance(inventory.get("harness_runtime"), dict) else {}
    if harness.get("required") is True:
        deployment["harness_runtime"] = {
            key: harness.get(key)
            for key in (
                "schema",
                "runtime_id",
                "runtime_type",
                "python_executable",
                "python_version",
                "python_abi",
                "lock_sha256",
                "manifest_path",
                "runtime_root",
                "materialization",
            )
            if harness.get(key) is not None
        }
    return deployment


def build_blocked_marker(run_dir: Path, resolved: dict, reason: str) -> dict:
    """Terminal marker for a run that stopped before the bundle was sealed.

    Hashes whatever terminal evidence does exist so the marker still identifies
    the run, and pins execution_policy.container_only to False so nothing
    downstream can read it as an Eval-ready bundle.
    """
    artifacts = run_dir / "artifacts"
    hashes = {
        f"artifacts/{name}": sha256(artifacts / name)
        for name in TERMINAL_FILES
        if name != "deployment_ready.json" and (artifacts / name).is_file()
    }
    return {
        "schema": "sure.onboard.deployment_ready.v1",
        "generated_at": now_iso(),
        "status": "blocked",
        "blocked_reason": reason,
        "model_name": str(resolved["model_name"]),
        "package_profile": "docker-registry",
        "required_artifact_sha256": hashes,
        "bundle_identity_sha256": hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "execution_policy": {
            "container_only": False,
            "nfs_models_read_only": True,
            "host_python_fallback": False,
            "approved_image_override": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument(
        "--blocked",
        metavar="REASON",
        help="seal the run as blocked instead of ready, recording REASON",
    )
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    if args.blocked:
        marker = build_blocked_marker(run_dir, resolved, args.blocked)
        write_identical(artifacts / "deployment_ready.json", json_bytes(marker))
        print(artifacts / "deployment_ready.json")
        return 0
    registry = read_object(artifacts / "docker_registry_result.json")
    model_dir = resolve_model_dir(resolved)
    model_artifacts = model_dir / "artifacts"
    model_artifacts.mkdir(parents=True, exist_ok=True)

    stage_wrapper(run_dir / "adapter", model_dir)
    stage_fixture(run_dir, model_dir, resolved)
    stage_artifacts(run_dir, model_dir)
    package = write_package_gate(run_dir, model_dir, registry)
    write_artifact_manifest(run_dir, model_dir, resolved)
    deployment = build_deployment_ready(run_dir, model_dir, resolved, registry, package)
    content = json_bytes(deployment)
    write_identical(model_artifacts / "deployment_ready.json", content)
    write_identical(artifacts / "deployment_ready.json", content)
    print(artifacts / "deployment_ready.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
