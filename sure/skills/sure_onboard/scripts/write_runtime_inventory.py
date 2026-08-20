#!/usr/bin/env python3
"""Write the portable v2 runtime contract after the package gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - harness dependency
    yaml = None

from deployment_contract import (
    immutable_image_ref,
    load_artifact,
    read_json,
    resolve_model_dir,
    sha256_file,
    validate_container_documents,
    validate_runtime_roles,
)


SCHEMA = "sure.onboard.runtime_inventory.v2"
CORE_FILES = ("model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml", "Dockerfile")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def relative_existing(raw: object, model_dir: Path) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw).expanduser()
    candidate = path if path.is_absolute() else model_dir / path
    try:
        return str(candidate.resolve().relative_to(model_dir.resolve())) if candidate.exists() else None
    except ValueError:
        return None


def load_config(model_dir: Path) -> dict[str, Any]:
    if yaml is None or not (model_dir / "config.yaml").is_file():
        return {}
    value = yaml.safe_load((model_dir / "config.yaml").read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def core_identity(model_dir: Path) -> tuple[dict[str, str], str]:
    hashes: dict[str, str] = {}
    for name in CORE_FILES:
        path = model_dir / name
        if path.is_file():
            hashes[name] = sha256_file(path)
    for name in (
        "artifacts/weights_manifest.json",
        "artifacts/docker_build_result.json",
        "artifacts/docker_validation.json",
        "artifacts/docker_registry_result.json",
        "artifacts/package_gate.json",
    ):
        path = model_dir / name
        if path.is_file():
            hashes[name] = sha256_file(path)
    encoded = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    return hashes, hashlib.sha256(encoded).hexdigest()


def normalize_command(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def build_inventory(model_dir: Path, run_dir: Path) -> dict[str, Any]:
    resolved = read_json(run_dir / "artifacts" / "model_input_resolved.json")
    package = load_artifact(run_dir, model_dir, "package_gate.json")
    build_env = load_artifact(run_dir, model_dir, "build_env_result.json")
    weights = load_artifact(run_dir, model_dir, "weights_manifest.json")
    config = load_config(model_dir)
    deployment_type = str(resolved.get("deployment_type") or "local")
    profile = str(package.get("package_profile") or resolved.get("package_profile") or "none")
    readiness = package.get("readiness") if isinstance(package.get("readiness"), dict) else {}
    hashes, bundle_identity = core_identity(model_dir)

    local_python = relative_existing(build_env.get("python_executable"), model_dir)
    lockfile = relative_existing(build_env.get("lockfile_path"), model_dir)
    local_runtime = {
        "purpose": "onboard_validation_evidence_only",
        "eligible_for_eval": False,
        "backend": build_env.get("backend"),
        "python_executable": local_python,
        "python_version": build_env.get("python_version"),
        "lockfiles": [lockfile] if lockfile else [],
    }

    if deployment_type == "api":
        status = "api_ready" if readiness.get("local_ready") is True else "partial"
        container_runtime: dict[str, Any] = {"required": False}
        model_runtime: dict[str, Any] = {"required": False, "runtime_type": "api"}
        harness_runtime: dict[str, Any] = {"required": False, "runtime_type": "harness_python"}
        eval_runtime = "api_only" if status == "api_ready" else "unavailable"
    elif profile == "docker-registry" and readiness.get("bundle_ready") is True:
        build = load_artifact(run_dir, model_dir, "docker_build_result.json")
        validation = load_artifact(run_dir, model_dir, "docker_validation.json")
        registry = load_artifact(run_dir, model_dir, "docker_registry_result.json")
        image, digest, image_ref = validate_container_documents(build, validation, registry)
        runtime = validation.get("runtime") if isinstance(validation.get("runtime"), dict) else {}
        validated_model_runtime, validated_harness_runtime = validate_runtime_roles(validation)
        model_runtime = {
            "required": True,
            "runtime_type": "model_python",
            "python_executable": validated_model_runtime.get("python_executable"),
            "python_version": validated_model_runtime.get("python_version"),
            "checks": validated_model_runtime.get("checks"),
        }
        harness_runtime = {
            "required": True,
            "schema": validated_harness_runtime.get("schema"),
            "runtime_id": validated_harness_runtime.get("runtime_id"),
            "runtime_type": "harness_python",
            "python_executable": validated_harness_runtime.get("python_executable"),
            "python_version": validated_harness_runtime.get("python_version"),
            "python_abi": validated_harness_runtime.get("python_abi"),
            "lock_sha256": validated_harness_runtime.get("lock_sha256"),
            "manifest_path": validated_harness_runtime.get("manifest_path"),
            "runtime_root": validated_harness_runtime.get("runtime_root"),
            "materialization": validated_harness_runtime.get("materialization"),
            "checks": validated_harness_runtime.get("checks"),
        }
        server = config.get("server") if isinstance(config.get("server"), dict) else {}
        server_command = normalize_command(runtime.get("server_command")) or normalize_command(server.get("command"))
        if server_command and server_command[0].startswith(".venv/"):
            server_command[0] = str(runtime.get("python_executable") or "python")
        container_runtime = {
            "required": True,
            "base_image": build.get("base_image"),
            "target_image": image,
            "target_image_digest": digest,
            "target_image_ref": image_ref,
            "dockerfile": {"path": str(build.get("dockerfile_path") or "Dockerfile"), "sha256": build["dockerfile_sha256"]},
            "python_executable": model_runtime["python_executable"],
            "working_dir": runtime.get("working_dir") or "/workspace/model",
            "server_command": server_command or ["python", "server.py"],
            "tool_names": [
                str(tool["name"])
                for tool in config.get("tools", [])
                if isinstance(tool, dict) and isinstance(tool.get("name"), str)
            ],
            "gpu_required": bool((config.get("resources") or {}).get("gpu", True))
            if isinstance(config.get("resources"), dict)
            else True,
            "mount_policy": {
                "nfs_models_read_only": True,
                "model_bundle": {"source": ".", "target": runtime.get("working_dir") or "/workspace/model", "read_only": True},
                "result_workspace": {"source": "sure/results", "target": "/sure-output", "read_only": False},
            },
        }
        status = "ready"
        eval_runtime = "container_only"
    else:
        status = "local_only" if readiness.get("local_ready") is True else "partial"
        container_runtime = {"required": profile != "none"}
        model_runtime = {"required": deployment_type == "local", "runtime_type": "model_python"}
        harness_runtime = {"required": deployment_type == "local", "runtime_type": "harness_python"}
        eval_runtime = "unavailable"

    model_cfg = config.get("model") if isinstance(config.get("model"), dict) else {}
    return {
        "schema": SCHEMA,
        "generated_at": now_iso(),
        "status": status,
        "model": {
            "name": str(resolved.get("model_name") or model_dir.name),
            "id": resolved.get("model_id") or model_cfg.get("id"),
            "task": resolved.get("task_type") or config.get("task"),
            "deployment_type": deployment_type,
            "bundle_root": ".",
            "bundle_identity_sha256": bundle_identity,
        },
        "local_runtime": local_runtime,
        "model_runtime": model_runtime,
        "harness_runtime": harness_runtime,
        "container_runtime": container_runtime,
        "weights": {
            "manifest_path": "artifacts/weights_manifest.json",
            "manifest_sha256": hashes.get("artifacts/weights_manifest.json"),
            "ready": weights.get("weights_ready") is True or weights.get("status") == "fetched" or weights.get("required") is False,
        },
        "readiness": readiness,
        "evidence": {
            "package_gate": "artifacts/package_gate.json",
            "docker_build": "artifacts/docker_build_result.json" if deployment_type == "local" else None,
            "docker_validation": "artifacts/docker_validation.json" if deployment_type == "local" else None,
            "docker_registry": "artifacts/docker_registry_result.json" if deployment_type == "local" else None,
            "core_sha256": hashes,
        },
        "policy": {
            "eval_runtime": eval_runtime,
            "host_python_fallback": False,
            "image_override_allowed": False,
            "nfs_models_mutable_by_eval": False,
        },
    }


def write_inventory(model_dir: Path, produces: Path, run_dir: Path) -> dict[str, Any]:
    inventory = build_inventory(model_dir.resolve(), run_dir.resolve())
    write_json(produces.resolve(), inventory)
    model_output = model_dir.resolve() / "artifacts" / "runtime_inventory.json"
    if model_output != produces.resolve():
        write_json(model_output, inventory)
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--produces", required=True, type=Path)
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    try:
        inferred, _ = resolve_model_dir(args.run_dir.resolve())
        model_dir = args.model_dir.expanduser().resolve() if args.model_dir else inferred
        inventory = write_inventory(model_dir, args.produces, args.run_dir)
    except (OSError, ValueError) as exc:
        print(f"write_runtime_inventory failed: {exc}", file=sys.stderr)
        return 1
    print(f"write_runtime_inventory OK: status={inventory['status']}, model={inventory['model']['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
