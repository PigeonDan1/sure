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

from deployment_contract import (
    normalize_harness_runtime,
    read_json,
    resolve_model_dir,
    sha256_file,
    timestamp_after,
)
from write_package_gate import write_package_gate
from write_runtime_inventory import write_inventory
from write_verdict import write_verdict


CORE_TERMINAL_ARTIFACTS = (
    "package_gate.json",
    "runtime_inventory.json",
    "verdict.json",
)
MODEL_RUNTIME_ARTIFACT = "model_runtime_manifest.json"
DELIVERY_ARTIFACTS = (
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


def ensure_safe_bundle_targets(model_dir: Path, resolved: dict[str, Any]) -> None:
    declared = Path(str(resolved.get("model_dir") or "")).expanduser()
    if declared.is_symlink():
        raise ValueError("model_dir must be a real harness-owned directory, not a symlink")
    if declared.resolve() != model_dir.resolve():
        raise ValueError("resolved model_dir disagrees with the declared bundle path")
    artifacts = model_dir / "artifacts"
    if artifacts.is_symlink():
        raise ValueError("model artifacts directory must not be a symlink")
    artifacts.mkdir(parents=True, exist_ok=True)
    if not artifacts.is_dir() or not artifacts.resolve().is_relative_to(model_dir.resolve()):
        raise ValueError("model artifacts directory escapes the model bundle")
    for name in (
        *CORE_TERMINAL_ARTIFACTS,
        MODEL_RUNTIME_ARTIFACT,
        *DELIVERY_ARTIFACTS,
        "artifact_manifest.json",
        "deployment_ready.json",
    ):
        target = artifacts / name
        if target.is_symlink():
            raise ValueError(f"model artifact target must not be a symlink: {target}")


def copy_selected_delivery_artifacts(run_dir: Path, model_dir: Path, resolved: dict[str, Any]) -> None:
    profile = str(resolved.get("package_profile") or "none")
    deployment_type = str(resolved.get("deployment_type") or "local")
    names: tuple[str, ...]
    if profile == "docker-registry":
        names = DELIVERY_ARTIFACTS
    elif profile == "docker-local":
        names = DELIVERY_ARTIFACTS[:2]
    elif deployment_type == "local" and profile == "none":
        names = (MODEL_RUNTIME_ARTIFACT,)
    else:
        names = ()
    for name in names:
        source = run_dir / "artifacts" / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"selected run delivery artifact is missing or unsafe: {source}")
        destination = model_dir / "artifacts" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def resolve_weights_root(model_dir: Path, weights_manifest: dict[str, Any]) -> Path | None:
    """Locate the model-local weights root, or None when the weights live outside.

    check_weights.py allows weights to stay outside the bundle as long as the
    manifest sets fallback_to_host_global with a reason, and sure_feed writes a
    default local_dir_name for every model, so a missing model-local root is a
    normal shape rather than an error. Such weights are not hashed into the
    bundle identity; see weights_integrity.
    """
    external = bool(weights_manifest.get("fallback_to_host_global"))
    candidates = (
        ("local_dir_name", weights_manifest.get("local_dir_name")),
        ("checkpoint_root", weights_manifest.get("checkpoint_root")),
        ("resolved_local_model_path", weights_manifest.get("resolved_local_model_path")),
    )
    for field, raw in candidates:
        if not isinstance(raw, str) or not raw.strip():
            continue
        value = Path(raw).expanduser()
        if field == "local_dir_name" and (value.is_absolute() or ".." in value.parts):
            raise ValueError(f"weights local_dir_name is not portable: {raw}")
        candidate = value if value.is_absolute() else model_dir / value
        resolved = candidate.resolve()
        if resolved == model_dir.resolve() or not resolved.is_relative_to(model_dir.resolve()):
            if external:
                return None
            raise ValueError(f"weights {field} must resolve below the model bundle: {raw}")
        if not resolved.exists():
            if external:
                return None
            raise ValueError(f"weights {field} does not exist: {raw}")
        return resolved
    if weights_manifest.get("required") is True and not external:
        raise ValueError(
            "required weights have no model-local root; declare local_dir_name, "
            "checkpoint_root, or resolved_local_model_path"
        )
    return None


def weights_integrity(model_dir: Path, weights_manifest: dict[str, Any]) -> str:
    return "bundled" if resolve_weights_root(model_dir, weights_manifest) is not None else "external"


def update_manifest(model_dir: Path, resolved: dict[str, Any]) -> dict[str, Any]:
    manifest_path = model_dir / "artifacts" / "artifact_manifest.json"
    manifest = read_json(manifest_path)
    required = manifest.setdefault("artifacts", {}).setdefault("required", {})
    profile = str(resolved.get("package_profile") or "none")
    deployment_type = str(resolved.get("deployment_type") or "local")
    generated_paths = {
        *(f"artifacts/{name}" for name in CORE_TERMINAL_ARTIFACTS),
        f"artifacts/{MODEL_RUNTIME_ARTIFACT}",
        *(f"artifacts/{name}" for name in DELIVERY_ARTIFACTS),
        "artifacts/artifact_manifest.json",
        "artifacts/deployment_ready.json",
    }
    for key, entry in list(required.items()):
        if isinstance(entry, dict) and entry.get("path") in generated_paths:
            required.pop(key)
    delivery_required = (
        DELIVERY_ARTIFACTS
        if profile == "docker-registry"
        else DELIVERY_ARTIFACTS[:2]
        if profile == "docker-local"
        else ()
    )
    runtime_required = (
        (MODEL_RUNTIME_ARTIFACT,)
        if deployment_type == "local" and profile == "none"
        else ()
    )
    for name in (
        *CORE_TERMINAL_ARTIFACTS,
        *runtime_required,
        *delivery_required,
        "artifact_manifest.json",
        "deployment_ready.json",
    ):
        key = name.replace(".", "_").replace("-", "_")
        required[key] = {"path": f"artifacts/{name}", "description": f"Finalized onboard artifact: {name}."}
    ready_profile = deployment_type == "local" and profile in {"none", "docker-registry"}
    if profile == "docker-registry" and not (model_dir / "Dockerfile").is_file():
        raise ValueError("docker-registry bundle is missing Dockerfile")
    if (model_dir / "Dockerfile").is_file():
        required["dockerfile"] = {"path": "Dockerfile", "description": "Container build file."}
    sample_output = model_dir / "artifacts" / "sample_output.json"
    if ready_profile and not sample_output.is_file():
        raise ValueError("ready local bundle is missing artifacts/sample_output.json")
    if sample_output.is_file():
        required["sample_output_json"] = {
            "path": "artifacts/sample_output.json",
            "description": "Bounded inference sample output.",
        }
    fixture_root = model_dir / "fixture"
    fixture_files = sorted(path for path in fixture_root.rglob("*") if path.is_file()) if fixture_root.is_dir() else []
    if ready_profile and not any(path.name == "gt.jsonl" for path in fixture_files):
        raise ValueError("ready local bundle is missing fixture ground truth")
    if fixture_root.is_dir():
        for fixture_path in fixture_files:
            relative = fixture_path.relative_to(model_dir).as_posix()
            key = f"file:{relative}"
            existing = required.get(key)
            if isinstance(existing, dict) and existing.get("path") != relative:
                raise ValueError(f"duplicate finalized artifact manifest key: {key}")
            required[key] = {"path": relative, "description": f"Bounded smoke fixture file: {relative}."}
    weights_manifest_path = model_dir / "artifacts" / "weights_manifest.json"
    if weights_manifest_path.is_file():
        weights_manifest = read_json(weights_manifest_path)
        weights_root = resolve_weights_root(model_dir, weights_manifest)
        if weights_root is not None:
            weight_files = (
                [weights_root]
                if weights_root.is_file()
                else sorted(weight_path for weight_path in weights_root.rglob("*") if weight_path.is_file())
            )
            if weights_manifest.get("required") is True and not weight_files:
                raise ValueError(f"required weights root contains no files: {weights_root}")
            for weight_path in weight_files:
                relative = weight_path.relative_to(model_dir).as_posix()
                key = f"file:{relative}"
                existing = required.get(key)
                if isinstance(existing, dict) and existing.get("path") != relative:
                    raise ValueError(f"weight file conflicts with another required bundle file: {relative}")
                required[key] = {"path": relative, "description": f"Model weight file: {relative}."}
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
    atomic_write(manifest_path, json_bytes(manifest))
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

    manifest = read_json(model_dir / "artifacts" / "artifact_manifest.json")
    required = manifest.get("artifacts", {}).get("required") if isinstance(manifest.get("artifacts"), dict) else {}
    if not isinstance(required, dict) or not required:
        raise ValueError("finalized artifact manifest has no required entries")
    hashes: dict[str, str] = {}
    for entry in required.values():
        if not isinstance(entry, dict):
            raise ValueError("finalized artifact manifest entry is invalid")
        raw_path = str(entry.get("path") or "")
        relative = Path(raw_path)
        if not raw_path or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"finalized artifact path is not portable: {raw_path}")
        if relative.as_posix() == "artifacts/deployment_ready.json":
            continue
        path = (model_dir / relative).resolve()
        if not path.is_file() or not path.is_relative_to(model_dir.resolve()):
            raise ValueError(f"finalized required artifact is missing: {raw_path}")
        hashes[relative.as_posix()] = sha256_file(path)
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
        "integrity_profile": "manifest-complete-v1",
        "generated_at": timestamp_after(
            ("artifact_manifest.json", manifest),
            ("package_gate.json", package),
            ("runtime_inventory.json", inventory),
            ("verdict.json", verdict),
        ),
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
    weights_manifest_path = model_dir / "artifacts" / "weights_manifest.json"
    if weights_manifest_path.is_file():
        deployment["weights_integrity"] = weights_integrity(model_dir, read_json(weights_manifest_path))
    if python_delivery:
        deployment["model_runtime"] = model_runtime if status == "ready" else {}
    return deployment


def finalize(run_dir: Path, produces: Path) -> dict[str, Any]:
    model_dir, resolved = resolve_model_dir(run_dir)
    ensure_safe_bundle_targets(model_dir, resolved)
    copy_selected_delivery_artifacts(run_dir, model_dir, resolved)
    update_manifest(model_dir, resolved)
    package = write_package_gate(run_dir, run_dir / "artifacts" / "package_gate.json", model_dir)
    write_inventory(model_dir, run_dir / "artifacts" / "runtime_inventory.json", run_dir)
    write_verdict(run_dir, run_dir / "artifacts" / "verdict.json", model_dir)
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
