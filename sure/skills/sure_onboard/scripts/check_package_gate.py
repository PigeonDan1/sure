#!/usr/bin/env python3
"""Cross-check local validation and Docker delivery readiness."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from deployment_contract import (
    load_artifact,
    resolve_model_dir,
    sha256_file,
    validate_container_documents,
)

PACKAGE_PROFILES = {"none", "docker-local", "docker-registry"}
STAGE_RESULTS = {
    "import_result.json": "import_passed",
    "load_result.json": "load_passed",
    "infer_result.json": "infer_passed",
    "contract_result.json": "contract_passed",
}
ENV_COMPAT_RESULT = "env_compat_result.json"


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a JSON object.")
    return data


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def infer_model_dir(manifest: dict[str, Any], manifest_path: Path) -> Path | None:
    raw = manifest.get("model_dir") or manifest.get("model_root")
    if raw:
        return Path(str(raw)).expanduser()
    if manifest_path.parent.name == "artifacts":
        return manifest_path.parent.parent
    return None


def resolve_manifest_path(raw: object, run_dir: Path, model_dir: Path | None) -> Path | None:
    candidates: list[Path] = []
    if raw:
        path = Path(str(raw)).expanduser()
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend([run_dir / "artifacts" / path, run_dir / path])
            if model_dir:
                candidates.extend([model_dir / path, model_dir / "artifacts" / path])
    candidates.append(run_dir / "artifacts" / "artifact_manifest.json")
    if model_dir:
        candidates.append(model_dir / "artifacts" / "artifact_manifest.json")
    return first_existing(candidates)


def resolve_artifact(name: str, run_dir: Path, model_dir: Path | None) -> Path | None:
    candidates = [run_dir / "artifacts" / name]
    if model_dir:
        candidates.append(model_dir / "artifacts" / name)
    return first_existing(candidates)


def check_artifact_manifest(run_dir: Path, manifest_path: Path) -> tuple[bool, str]:
    script = Path(__file__).with_name("check_artifact_manifest.py")
    proc = subprocess.run(
        [sys.executable, str(script), "--run-dir", str(run_dir), "--produces", str(manifest_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True, proc.stdout.strip()
    return False, (proc.stderr or proc.stdout).strip()


def validation_results_pass(run_dir: Path, model_dir: Path | None) -> tuple[bool, str]:
    missing: list[str] = []
    failed: list[str] = []
    env_compat = resolve_artifact(ENV_COMPAT_RESULT, run_dir, model_dir)
    if not env_compat:
        missing.append(ENV_COMPAT_RESULT)
    else:
        try:
            data = load_json(env_compat)
        except ValueError as exc:
            failed.append(f"{ENV_COMPAT_RESULT}: {exc}")
        else:
            if data.get("compat_ok") is not True:
                failed.append(f"{ENV_COMPAT_RESULT}.compat_ok is not true")
    for filename, pass_key in STAGE_RESULTS.items():
        path = resolve_artifact(filename, run_dir, model_dir)
        if not path:
            missing.append(filename)
            continue
        try:
            data = load_json(path)
        except ValueError as exc:
            failed.append(f"{filename}: {exc}")
            continue
        if data.get(pass_key) is not True:
            failed.append(f"{filename}.{pass_key} is not true")
    if missing or failed:
        parts = []
        if missing:
            parts.append("missing validation artifacts: " + ", ".join(missing))
        if failed:
            parts.append("failed validation artifacts: " + "; ".join(failed))
        return False, "; ".join(parts)
    sample = resolve_artifact("sample_output.json", run_dir, model_dir)
    if not sample:
        return False, "sample_output.json is required after VALIDATE_INFER."
    try:
        load_json(sample)
    except ValueError as exc:
        return False, str(exc)
    return True, "validation artifacts passed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    path = Path(args.produces)
    if not path.exists():
        print(f"package_gate.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = load_json(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    status = data.get("status")
    if status != "passed":
        print(f"PACKAGE_GATE failed: status must be passed, got {status!r}.", file=sys.stderr)
        return 1
    if data.get("schema") != "sure.onboard.package_gate.v2":
        print("PACKAGE_GATE failed: schema must be sure.onboard.package_gate.v2.", file=sys.stderr)
        return 1

    package_profile = data.get("package_profile")
    if package_profile not in PACKAGE_PROFILES:
        print(f"PACKAGE_GATE failed: unsupported package_profile={package_profile!r}.", file=sys.stderr)
        return 1

    readiness = data.get("readiness")
    if not isinstance(readiness, dict):
        print("PACKAGE_GATE failed: readiness must be an object.", file=sys.stderr)
        return 1
    if readiness.get("local_ready") is not True:
        print("PACKAGE_GATE failed: local_ready must be true before any final verdict.", file=sys.stderr)
        return 1

    try:
        model_dir, resolved = resolve_model_dir(run_dir)
    except (OSError, ValueError) as exc:
        print(f"PACKAGE_GATE failed: {exc}", file=sys.stderr)
        return 1
    manifest_path = resolve_manifest_path(data.get("artifact_manifest_path"), run_dir, model_dir)
    if not manifest_path:
        print(
            "PACKAGE_GATE failed: artifact_manifest.json is required after SAVE_ARTIFACTS "
            "(expected under <run_dir>/artifacts/ or <model_dir>/artifacts/).",
            file=sys.stderr,
        )
        return 1
    try:
        manifest_data = load_json(manifest_path)
    except ValueError as exc:
        print(f"PACKAGE_GATE failed: {exc}", file=sys.stderr)
        return 1
    manifest_ok, manifest_message = check_artifact_manifest(run_dir, manifest_path)
    if not manifest_ok:
        print(f"PACKAGE_GATE failed: {manifest_message}", file=sys.stderr)
        return 1

    validation_ok, validation_message = validation_results_pass(run_dir, model_dir)
    if not validation_ok:
        print(f"PACKAGE_GATE failed: {validation_message}", file=sys.stderr)
        return 1

    local = data.get("local") if isinstance(data.get("local"), dict) else {}
    if local and (local.get("validation_passed") is False or local.get("artifacts_complete") is False):
        print("PACKAGE_GATE failed: local.validation_passed and local.artifacts_complete must not be false.", file=sys.stderr)
        return 1

    deployment_type = resolved.get("deployment_type")
    if package_profile != resolved.get("package_profile"):
        print("PACKAGE_GATE failed: package_profile disagrees with model_input_resolved.json.", file=sys.stderr)
        return 1
    if deployment_type == "api":
        if package_profile != "none" or readiness.get("bundle_ready") is not True:
            print("PACKAGE_GATE failed: API deployment requires package=none and bundle_ready=true.", file=sys.stderr)
            return 1
    if deployment_type == "local" and package_profile in {"docker-local", "docker-registry"} and readiness.get("docker_ready") is not True:
        print(
            f"PACKAGE_GATE failed: package={package_profile} requires readiness.docker_ready=true.",
            file=sys.stderr,
        )
        return 1
    if deployment_type == "local" and package_profile in {"docker-local", "docker-registry"}:
        try:
            build = load_artifact(run_dir, model_dir, "docker_build_result.json")
            validation = load_artifact(run_dir, model_dir, "docker_validation.json")
            docker = data.get("docker") if isinstance(data.get("docker"), dict) else {}
            dockerfile = model_dir / str(docker.get("dockerfile_path") or "Dockerfile")
            if not dockerfile.is_file() or docker.get("dockerfile_sha256") != sha256_file(dockerfile):
                raise ValueError("package gate Dockerfile hash does not match the model bundle")
            if package_profile == "docker-registry":
                registry_result = load_artifact(run_dir, model_dir, "docker_registry_result.json")
                image, digest, image_ref = validate_container_documents(build, validation, registry_result)
                if any(
                    docker.get(key) != expected
                    for key, expected in (
                        ("target_image", image),
                        ("target_image_digest", digest),
                        ("target_image_ref", image_ref),
                    )
                ):
                    raise ValueError("package gate image binding disagrees with container evidence")
        except (OSError, ValueError) as exc:
            print(f"PACKAGE_GATE failed: {exc}", file=sys.stderr)
            return 1
    if deployment_type == "local" and package_profile == "docker-registry" and readiness.get("registry_ready") is not True:
        print(
            "PACKAGE_GATE failed: package=docker-registry requires readiness.registry_ready=true.",
            file=sys.stderr,
        )
        return 1
    if deployment_type == "local" and package_profile == "docker-registry":
        if readiness.get("container_ready") is not True or readiness.get("bundle_ready") is not True:
            print("PACKAGE_GATE failed: docker-registry requires container_ready=true and bundle_ready=true.", file=sys.stderr)
            return 1
    if deployment_type == "local" and package_profile != "docker-registry" and readiness.get("bundle_ready") is not False:
        print("PACKAGE_GATE failed: non-registry local package must declare bundle_ready=false.", file=sys.stderr)
        return 1

    print(
        "check_package_gate OK: "
        f"package={package_profile}, local_ready={readiness.get('local_ready')}, "
        f"docker_ready={readiness.get('docker_ready')}, registry_ready={readiness.get('registry_ready')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
