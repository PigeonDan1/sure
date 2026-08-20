#!/usr/bin/env python3
"""Gate the Docker build, bounded validation, and registry pull closure."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from deployment_contract import (
    immutable_image_ref,
    load_artifact,
    passed,
    read_json,
    resolve_evidence_path,
    resolve_model_dir,
    sha256_file,
    validate_container_documents,
    validate_image_and_digest,
    validate_runtime_roles,
)


def verify_live_image(image_ref: str) -> None:
    proc = subprocess.run(
        ["docker", "image", "inspect", image_ref, "--format", "{{json .RepoDigests}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise ValueError(f"digest-pinned image is not inspectable after pull verification: {detail}")
    try:
        repo_digests = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("docker image inspect did not return RepoDigests JSON") from exc
    if not isinstance(repo_digests, list) or image_ref not in repo_digests:
        raise ValueError(f"docker image inspect RepoDigests does not contain {image_ref}")


def verify_live_runtimes(
    image_ref: str,
    model_runtime: dict[str, object],
    harness_runtime: dict[str, object],
) -> None:
    model_python = str(model_runtime["python_executable"])
    model_probe = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", model_python, image_ref, "-c", "import sys; print(sys.executable)"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if model_probe.returncode != 0:
        raise ValueError(f"Model Python probe failed in the digest-pinned image: {(model_probe.stderr or model_probe.stdout).strip()}")
    harness_python = str(harness_runtime["python_executable"])
    manifest_path = str(harness_runtime["manifest_path"])
    runtime_id = str(harness_runtime["runtime_id"])
    lock_sha256 = str(harness_runtime["lock_sha256"])
    required_imports = ["yaml", "structlog", "pydantic", "pydantic_settings", "rich", "typer"]
    code = (
        "import importlib,json,pathlib;"
        f"[importlib.import_module(x) for x in {required_imports!r}];"
        f"m=json.loads(pathlib.Path({manifest_path!r}).read_text());"
        f"assert m.get('runtime_id') == {runtime_id!r};"
        f"assert m.get('lock_sha256') == {lock_sha256!r}"
    )
    harness_probe = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", harness_python, image_ref, "-s", "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if harness_probe.returncode != 0:
        raise ValueError(
            "Harness Runtime probe failed in the digest-pinned image: "
            + (harness_probe.stderr or harness_probe.stdout).strip()
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    produces = Path(args.produces).expanduser().resolve()
    try:
        registry = read_json(produces)
        model_dir, resolved = resolve_model_dir(run_dir)
        profile = str(resolved.get("package_profile") or "")
        deployment_type = str(resolved.get("deployment_type") or "")
        if deployment_type == "api" or profile == "none":
            if registry.get("status") != "skipped" or not registry.get("reason"):
                raise ValueError("API/diagnostic packaging must emit status=skipped with a reason")
            print(f"check_container_package OK: deployment_type={deployment_type}, package={profile}, skipped")
            return 0

        build = load_artifact(run_dir, model_dir, "docker_build_result.json")
        validation = load_artifact(run_dir, model_dir, "docker_validation.json")
        if profile == "docker-local":
            image, digest, image_ref = validate_image_and_digest(
                validation.get("target_image"), validation.get("target_image_digest")
            )
            if not passed(build.get("status")) or not passed(validation.get("status")):
                raise ValueError("docker-local requires passing build and validation results")
            if build.get("target_image") != image or build.get("target_image_digest") != digest:
                raise ValueError("docker-local build and validation image binding disagree")
        elif profile == "docker-registry":
            image, digest, image_ref = validate_container_documents(build, validation, registry)
        else:
            raise ValueError(f"unsupported package_profile={profile!r}")

        dockerfile = model_dir / str(build.get("dockerfile_path") or "Dockerfile")
        if not dockerfile.is_file():
            raise ValueError(f"Dockerfile does not exist: {dockerfile}")
        if build.get("dockerfile_sha256") != sha256_file(dockerfile):
            raise ValueError("docker_build_result.json dockerfile_sha256 does not match the actual Dockerfile")
        sample = resolve_evidence_path(validation.get("sample_output_path"), run_dir, model_dir)
        if sample is None or not sample.is_file() or sample.stat().st_size == 0:
            raise ValueError("docker_validation.json must reference a non-empty sample_output_path")
        expected_sample_hash = validation.get("sample_output_sha256")
        if expected_sample_hash != sha256_file(sample):
            raise ValueError("docker validation sample_output_sha256 does not match the referenced file")
        if validation.get("target_image_ref") != immutable_image_ref(image, digest):
            raise ValueError("docker validation did not run against the digest-pinned target image")
        model_runtime, harness_runtime = validate_runtime_roles(
            validation,
            expected_runtime_id=os.environ.get("SURE_HARNESS_RUNTIME_ID") or None,
            expected_lock_sha256=os.environ.get("SURE_HARNESS_LOCK_SHA256") or None,
        )
        verify_live_image(image_ref)
        verify_live_runtimes(image_ref, model_runtime, harness_runtime)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"PACKAGE_CONTAINER failed: {exc}", file=sys.stderr)
        return 1
    print(f"check_container_package OK: package={profile}, image={image_ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
