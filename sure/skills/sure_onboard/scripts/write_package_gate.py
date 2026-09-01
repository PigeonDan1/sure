#!/usr/bin/env python3
"""Generate package_gate.json from the preceding onboard evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from check_package_gate import validate_model_runtime
from deployment_contract import (
    load_model_artifact,
    passed,
    read_json,
    resolve_model_dir,
    sha256_file,
    timestamp_after,
    validate_container_documents,
    validate_image_and_digest,
    validate_runtime_roles,
)


STAGE_RESULTS = {
    "import_result.json": "import_passed",
    "load_result.json": "load_passed",
    "infer_result.json": "infer_passed",
    "contract_result.json": "contract_passed",
}
CORE_FILES = ("model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml")
GENERATED_TERMINAL_PATHS = {
    "artifacts/package_gate.json",
    "artifacts/runtime_inventory.json",
    "artifacts/verdict.json",
    "artifacts/deployment_ready.json",
}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def model_manifest(model_dir: Path) -> dict[str, Any]:
    path = model_dir / "artifacts" / "artifact_manifest.json"
    manifest = read_json(path)
    if manifest.get("status") not in {"staged", "finalized"}:
        raise ValueError("artifact_manifest.json must be staged or finalized")
    declared_model_dir = str(manifest.get("model_dir") or "")
    if declared_model_dir not in {".", str(model_dir)}:
        raise ValueError("artifact_manifest.json model_dir disagrees with the resolved model bundle")
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    required = artifacts.get("required") if isinstance(artifacts.get("required"), dict) else {}
    if not required:
        raise ValueError("artifact_manifest.json has no required artifacts")
    for name, entry in required.items():
        if not isinstance(entry, dict):
            raise ValueError(f"artifact_manifest.json required entry is invalid: {name}")
        raw = str(entry.get("path") or "")
        relative = Path(raw)
        if not raw or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"artifact_manifest.json required path is not portable: {raw!r}")
        if relative.as_posix() in GENERATED_TERMINAL_PATHS:
            continue
        if not (model_dir / relative).is_file():
            raise ValueError(f"artifact_manifest.json required artifact is missing: {raw}")
    return manifest


def validate_local_evidence(run_dir: Path, model_dir: Path) -> None:
    build_env = load_model_artifact(model_dir, "build_env_result.json")
    if build_env.get("env_ready") is not True:
        raise ValueError("build_env_result.json must prove env_ready=true")
    env_compat = load_model_artifact(model_dir, "env_compat_result.json")
    if env_compat.get("compat_ok") is not True:
        raise ValueError("env_compat_result.json must prove compat_ok=true")
    for filename, pass_key in STAGE_RESULTS.items():
        result = load_model_artifact(model_dir, filename)
        if result.get(pass_key) is not True:
            raise ValueError(f"{filename} must prove {pass_key}=true")
    sample = load_model_artifact(model_dir, "sample_output.json")
    if not sample:
        raise ValueError("sample_output.json must contain a non-empty JSON object")


def validate_local_container(build: dict[str, Any], validation: dict[str, Any]) -> tuple[str, str, str]:
    if not passed(build.get("status")) or not passed(validation.get("status")):
        raise ValueError("docker build and validation status must both pass")
    image, digest, image_ref = validate_image_and_digest(
        build.get("target_image"), build.get("target_image_digest")
    )
    if validation.get("target_image") != image or validation.get("target_image_digest") != digest:
        raise ValueError("docker validation image binding disagrees with docker build evidence")
    if build.get("target_image_ref") != image_ref or validation.get("target_image_ref") != image_ref:
        raise ValueError("docker build and validation must use the same digest-pinned image reference")
    if build.get("base_image") == image:
        raise ValueError("base_image and target_image must differ")
    checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
    required_checks = ("import", "load", "infer", "contract", "bounded_fixture_inference")
    failed = [name for name in required_checks if not passed(checks.get(name))]
    if failed:
        raise ValueError("docker_validation.json has failed checks: " + ", ".join(failed))
    validate_runtime_roles(validation)
    return image, digest, image_ref


def build_package_gate(run_dir: Path, model_dir: Path) -> dict[str, Any]:
    resolved = read_json(run_dir / "artifacts" / "model_input_resolved.json")
    manifest = model_manifest(model_dir)
    missing_core = [name for name in CORE_FILES if not (model_dir / name).is_file()]
    if missing_core:
        raise ValueError("model bundle is missing core files: " + ", ".join(missing_core))
    validate_local_evidence(run_dir, model_dir)

    deployment_type = str(resolved.get("deployment_type") or "local")
    profile = str(resolved.get("package_profile") or "none")
    if deployment_type == "api" and profile != "none":
        raise ValueError("API deployment requires package_profile=none")

    container_ready = False
    docker_ready = False
    registry_ready = False
    docker: dict[str, Any] = {}
    registry_summary: dict[str, Any] = {}
    model_runtime_summary: dict[str, Any] = {}
    if deployment_type == "local" and profile in {"docker-local", "docker-registry"}:
        build = load_model_artifact(model_dir, "docker_build_result.json")
        validation = load_model_artifact(model_dir, "docker_validation.json")
        if profile == "docker-registry":
            registry = load_model_artifact(model_dir, "docker_registry_result.json")
            image, digest, image_ref = validate_container_documents(build, validation, registry)
            registry_ready = True
            registry_summary = {"push": "passed", "pull_verify": "passed"}
        else:
            image, digest, image_ref = validate_local_container(build, validation)
        dockerfile_path = str(build.get("dockerfile_path") or "Dockerfile")
        dockerfile = model_dir / dockerfile_path
        if not dockerfile.is_file() or build.get("dockerfile_sha256") != sha256_file(dockerfile):
            raise ValueError("docker_build_result.json Dockerfile hash does not match the model bundle")
        container_ready = True
        docker_ready = True
        docker = {
            "dockerfile_path": dockerfile_path,
            "dockerfile_sha256": sha256_file(dockerfile),
            "base_image": build.get("base_image"),
            "target_image": image,
            "target_image_digest": digest,
            "target_image_ref": image_ref,
            "build_result_path": "artifacts/docker_build_result.json",
            "validation_result_path": "artifacts/docker_validation.json",
        }
        if profile == "docker-registry":
            docker["registry_result_path"] = "artifacts/docker_registry_result.json"
    elif deployment_type == "local" and profile == "none":
        validate_model_runtime(run_dir, model_dir)
        runtime_manifest = load_model_artifact(model_dir, "model_runtime_manifest.json")
        model_runtime_summary = {
            key: runtime_manifest.get(key)
            for key in (
                "schema",
                "runtime_id",
                "runtime_type",
                "backend",
                "python_executable",
                "python_version",
                "python_abi",
                "lock_sha256",
            )
            if runtime_manifest.get(key) is not None
        }

    bundle_ready = deployment_type == "api" or (
        deployment_type == "local"
        and (profile == "none" or (profile == "docker-registry" and registry_ready))
    )
    readiness = {
        "local_ready": True,
        "container_ready": container_ready,
        "docker_ready": docker_ready,
        "registry_ready": registry_ready,
        "bundle_ready": bundle_ready,
        "vc_ready": None,
    }
    payload: dict[str, Any] = {
        "schema": "sure.onboard.package_gate.v2",
        "generated_at": timestamp_after(("artifact_manifest.json", manifest)),
        "status": "passed",
        "package_profile": profile,
        "model_dir": ".",
        "artifact_manifest_path": "artifacts/artifact_manifest.json",
        "readiness": readiness,
        "local": {"validation_passed": True, "artifacts_complete": True},
        "bundle": {},
        "notes": "Generated from validated onboard artifacts by write_package_gate.py.",
    }
    if docker:
        payload["docker"] = docker
    if registry_summary:
        payload["registry"] = registry_summary
    if model_runtime_summary:
        payload["model_runtime"] = model_runtime_summary
    return payload


def write_package_gate(run_dir: Path, produces: Path, model_dir: Path | None = None) -> dict[str, Any]:
    inferred, _ = resolve_model_dir(run_dir.resolve())
    target_model_dir = model_dir.resolve() if model_dir else inferred
    package = build_package_gate(run_dir.resolve(), target_model_dir)
    write_json(produces.resolve(), package)
    model_output = target_model_dir / "artifacts" / "package_gate.json"
    if model_output.resolve() != produces.resolve():
        write_json(model_output, package)
    return package


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--produces", required=True, type=Path)
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    try:
        package = write_package_gate(
            args.run_dir.expanduser().resolve(),
            args.produces.expanduser().resolve(),
            args.model_dir.expanduser().resolve() if args.model_dir else None,
        )
    except (OSError, ValueError) as exc:
        print(f"write_package_gate failed: {exc}", file=sys.stderr)
        return 1
    print(
        "write_package_gate OK: "
        f"package={package['package_profile']}, bundle_ready={package['readiness']['bundle_ready']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
