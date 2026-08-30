#!/usr/bin/env python3
"""Finalize a portable model bundle and write deployment_ready.json last."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deployment_contract import normalize_harness_runtime, read_json, resolve_model_dir, sha256_file


TERMINAL_ARTIFACTS = (
    "package_gate.json",
    "runtime_inventory.json",
    "verdict.json",
    "model_runtime_manifest.json",
    "docker_build_result.json",
    "docker_validation.json",
    "docker_registry_result.json",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def copy_terminal_artifacts(run_dir: Path, model_dir: Path, profile: str) -> None:
    for name in TERMINAL_ARTIFACTS:
        if profile == "none" and name.startswith("docker_"):
            continue
        source = run_dir / "artifacts" / name
        if source.is_file():
            destination = model_dir / "artifacts" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def update_package_gate(run_dir: Path, model_dir: Path) -> dict[str, Any]:
    path = run_dir / "artifacts" / "package_gate.json"
    package = read_json(path)
    package["model_dir"] = "."
    package["artifact_manifest_path"] = "artifacts/artifact_manifest.json"
    content = json_bytes(package)
    atomic_write(path, content)
    atomic_write(model_dir / "artifacts" / "package_gate.json", content)
    return package


def update_manifest(model_dir: Path, resolved: dict[str, Any]) -> dict[str, Any]:
    path = model_dir / "artifacts" / "artifact_manifest.json"
    manifest = read_json(path)
    required = manifest.setdefault("artifacts", {}).setdefault("required", {})
    profile = str(resolved.get("package_profile") or "none")
    finalized = [
        name
        for name in TERMINAL_ARTIFACTS
        if (profile != "none" or not name.startswith("docker_"))
        and (model_dir / "artifacts" / name).is_file()
    ]
    for name in (*finalized, "artifact_manifest.json", "deployment_ready.json"):
        key = name.replace(".", "_").replace("-", "_")
        required[key] = {"path": f"artifacts/{name}", "description": f"Finalized onboard artifact: {name}."}
    manifest.update(
        {
            "model_dir": ".",
            "model_id": resolved.get("model_id", ""),
            "model_name": resolved.get("model_name", model_dir.name),
            "phase": "deployment_ready",
            "status": "finalized",
            "timestamp": now_iso(),
        }
    )
    atomic_write(path, json_bytes(manifest))
    return manifest


def build_deployment_ready(run_dir: Path, model_dir: Path, resolved: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    inventory = read_json(model_dir / "artifacts" / "runtime_inventory.json")
    verdict = read_json(model_dir / "artifacts" / "verdict.json")
    profile = str(package.get("package_profile") or "none")
    deployment_type = str(resolved.get("deployment_type") or "local")
    python_delivery = deployment_type == "local" and profile == "none"
    success = str(verdict.get("status")) in {"passed", "success", "PASS", "PASSED", "pass"}
    if deployment_type == "local" and profile == "docker-registry" and success and inventory.get("status") == "ready":
        status = "ready"
    elif python_delivery and success and inventory.get("status") == "ready":
        status = "ready"
    elif deployment_type == "api" and success and inventory.get("status") == "api_ready":
        status = "api_ready"
    else:
        status = "local_only"

    required_names = ["runtime_inventory.json", "package_gate.json", "verdict.json", "artifact_manifest.json"]
    if status == "ready" and profile == "docker-registry":
        required_names.extend(["docker_build_result.json", "docker_validation.json", "docker_registry_result.json"])
    if status == "ready" and python_delivery:
        required_names.append("model_runtime_manifest.json")
    hashes = {
        f"artifacts/{name}": sha256_file(model_dir / "artifacts" / name)
        for name in required_names
    }
    bundle_identity = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    container = inventory.get("container_runtime") if isinstance(inventory.get("container_runtime"), dict) else {}
    harness_runtime = inventory.get("harness_runtime") if isinstance(inventory.get("harness_runtime"), dict) else {}
    model_runtime = inventory.get("model_runtime") if isinstance(inventory.get("model_runtime"), dict) else {}
    if status == "ready" and profile == "docker-registry":
        harness_runtime = normalize_harness_runtime(harness_runtime, allow_derive=False)
    execution_policy = (
        {
            "container_only": False,
            "eval_runtime": "python",
            "isolation": "trusted_host",
            "model_integrity": "verify_before_after",
            "nfs_models_read_only": False,
            "model_bundle_mutation_allowed": False,
            "host_python_fallback": False,
            "approved_image_override": False,
        }
        if python_delivery
        else {
            "container_only": status != "api_ready" and profile == "docker-registry",
            "nfs_models_read_only": True,
            "host_python_fallback": False,
            "approved_image_override": False,
        }
    )
    deployment = {
        "schema": (
            "sure.onboard.deployment_ready.v2"
            if python_delivery
            else "sure.onboard.deployment_ready.v1"
        ),
        "generated_at": now_iso(),
        "status": status,
        "model_name": str(resolved.get("model_name") or model_dir.name),
        "package_profile": profile,
        "target_image": container.get("target_image"),
        "target_image_digest": container.get("target_image_digest"),
        "target_image_ref": container.get("target_image_ref"),
        "harness_runtime": {
            key: harness_runtime.get(key)
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
            if harness_runtime.get(key) is not None
        },
        "runtime_inventory": "artifacts/runtime_inventory.json",
        "package_gate": "artifacts/package_gate.json",
        "verdict": "artifacts/verdict.json",
        "artifact_manifest": "artifacts/artifact_manifest.json",
        "required_artifact_sha256": hashes,
        "bundle_identity_sha256": bundle_identity,
        "execution_policy": execution_policy,
    }
    if python_delivery:
        deployment["model_runtime"] = model_runtime if status == "ready" else {}
    return deployment


def finalize(run_dir: Path, produces: Path) -> dict[str, Any]:
    model_dir, resolved = resolve_model_dir(run_dir)
    copy_terminal_artifacts(run_dir, model_dir, str(resolved.get("package_profile") or "none"))
    package = update_package_gate(run_dir, model_dir)
    update_manifest(model_dir, resolved)
    deployment = build_deployment_ready(run_dir, model_dir, resolved, package)
    content = json_bytes(deployment)
    atomic_write(model_dir / "artifacts" / "deployment_ready.json", content)
    atomic_write(produces, content)
    return deployment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--produces", required=True, type=Path)
    args = parser.parse_args()
    try:
        deployment = finalize(args.run_dir.expanduser().resolve(), args.produces.expanduser().resolve())
    except (OSError, ValueError) as exc:
        print(f"finalize_model_bundle failed: {exc}", file=sys.stderr)
        return 1
    print(f"finalize_model_bundle OK: status={deployment['status']}, model={deployment['model_name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
