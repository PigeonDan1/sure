#!/usr/bin/env python3
"""Generate verdict.json from validated build, runtime, and package evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from deployment_contract import load_model_artifact, read_json, resolve_model_dir, timestamp_after


STAGE_RESULTS = {
    "import_test": ("import_result.json", "import_passed"),
    "load_test": ("load_result.json", "load_passed"),
    "infer_test": ("infer_result.json", "infer_passed"),
    "contract_test": ("contract_result.json", "contract_passed"),
}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validation_evidence(run_dir: Path, model_dir: Path) -> dict[str, dict[str, Any]]:
    validation: dict[str, dict[str, Any]] = {}
    for test_name, (filename, pass_key) in STAGE_RESULTS.items():
        result = load_model_artifact(model_dir, filename)
        if result.get(pass_key) is not True:
            raise ValueError(f"{filename} must prove {pass_key}=true")
        duration = result.get("duration_ms")
        item: dict[str, Any] = {
            "passed": True,
            "error": result.get("error"),
        }
        if isinstance(duration, (int, float)):
            item["duration_ms"] = duration
        validation[test_name] = item
    return validation


def build_verdict(run_dir: Path, model_dir: Path) -> dict[str, Any]:
    resolved = read_json(run_dir / "artifacts" / "model_input_resolved.json")
    package = load_model_artifact(model_dir, "package_gate.json")
    runtime = load_model_artifact(model_dir, "runtime_inventory.json")
    build_env = load_model_artifact(model_dir, "build_env_result.json")
    backend = load_model_artifact(model_dir, "backend_choice.json")
    validation = validation_evidence(run_dir, model_dir)

    if package.get("status") != "passed":
        raise ValueError("package_gate.json must be passed")
    if build_env.get("env_ready") is not True:
        raise ValueError("build_env_result.json must prove env_ready=true")
    profile = str(package.get("package_profile") or "none")
    if profile != str(resolved.get("package_profile") or "none"):
        raise ValueError("package_gate.json profile disagrees with model_input_resolved.json")
    readiness = package.get("readiness") if isinstance(package.get("readiness"), dict) else {}
    runtime_readiness = runtime.get("readiness") if isinstance(runtime.get("readiness"), dict) else {}
    for key in ("local_ready", "docker_ready", "registry_ready", "bundle_ready"):
        if readiness.get(key) is not runtime_readiness.get(key):
            raise ValueError(f"runtime_inventory.json readiness.{key} disagrees with package_gate.json")

    deployment_type = str(resolved.get("deployment_type") or "local")
    if deployment_type == "local" and profile in {"none", "docker-registry"}:
        if runtime.get("status") != "ready" or readiness.get("bundle_ready") is not True:
            raise ValueError("ready local delivery verdict requires a ready runtime inventory")
        status = "passed"
    elif deployment_type == "api":
        if runtime.get("status") != "api_ready" or readiness.get("bundle_ready") is not True:
            raise ValueError("API verdict requires an api_ready runtime inventory")
        status = "passed"
    else:
        status = "partial"

    build: dict[str, Any] = {"success": True}
    duration = build_env.get("duration_seconds")
    if isinstance(duration, (int, float)):
        build["duration_seconds"] = duration
    if (model_dir / "artifacts" / "build_env.log").is_file():
        build["log_path"] = "artifacts/build_env.log"

    artifacts: dict[str, Any] = {
        "spec_path": "model.spec.yaml",
        "wrapper_path": ".",
        "artifact_manifest_path": "artifacts/artifact_manifest.json",
    }
    if (model_dir / "artifacts" / "validation.log").is_file():
        artifacts["validation_log_path"] = "artifacts/validation.log"
    if (model_dir / "artifacts" / "sample_output.json").is_file():
        artifacts["sample_output_path"] = "artifacts/sample_output.json"

    success = status == "passed"
    return {
        "instance_id": f"{resolved.get('model_name', model_dir.name)}-onboard",
        "timestamp": timestamp_after(
            ("package_gate.json", package),
            ("runtime_inventory.json", runtime),
        ),
        "model_id": str(resolved.get("model_id") or ""),
        "model_name": str(resolved.get("model_name") or model_dir.name),
        "status": status,
        "phase": "verdict",
        "tool_ready": readiness.get("local_ready") is True,
        "package": {"profile": profile},
        "readiness": readiness,
        "backend": {
            "type": str(backend.get("backend") or build_env.get("backend") or ""),
            "choice_reason": str(backend.get("choice_reason") or ""),
        },
        "build": build,
        "validation": validation,
        "failure": {
            "type": None if success else "package_incomplete",
            "category": None if success else "delivery",
            "message": None if success else "Local validation passed, but the selected package is not Eval-ready.",
            "retryable": not success,
        },
        "artifacts": artifacts,
        "notes": "Generated from validated onboard artifacts by write_verdict.py.",
    }


def write_verdict(run_dir: Path, produces: Path, model_dir: Path | None = None) -> dict[str, Any]:
    inferred, _ = resolve_model_dir(run_dir.resolve())
    target_model_dir = model_dir.resolve() if model_dir else inferred
    verdict = build_verdict(run_dir.resolve(), target_model_dir)
    write_json(produces.resolve(), verdict)
    model_output = target_model_dir / "artifacts" / "verdict.json"
    if model_output.resolve() != produces.resolve():
        write_json(model_output, verdict)
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--produces", required=True, type=Path)
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    try:
        verdict = write_verdict(
            args.run_dir.expanduser().resolve(),
            args.produces.expanduser().resolve(),
            args.model_dir.expanduser().resolve() if args.model_dir else None,
        )
    except (OSError, ValueError) as exc:
        print(f"write_verdict failed: {exc}", file=sys.stderr)
        return 1
    print(f"write_verdict OK: status={verdict['status']}, model={verdict['model_name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
