#!/usr/bin/env python3
"""Stage run artifacts into the model-local artifacts directory.

The Sure run directory is transient; the onboarded model directory is the
durable product. This helper is intentionally narrow: it copies already-created
run artifacts into ``sure/models/<model_name>/artifacts/`` and writes the
preferred ``artifact_manifest.json`` shape. It does not create wrappers, specs,
validation results, weights, or verdicts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from write_runtime_inventory import write_inventory


CORE_FILES = ["model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml"]
REQUIRED_RUN_ARTIFACTS = [
    "model_input_resolved.json",
    "context_selection.json",
    "repo_summary.json",
    "classification.json",
    "backend_choice.json",
    "build_plan.json",
    "spec_validation.json",
    "fixture_manifest.json",
    "build_env_result.json",
    "weights_manifest.json",
    "env_compat_result.json",
    "import_result.json",
    "load_result.json",
    "infer_result.json",
    "contract_result.json",
    "wrapper_manifest.json",
]
OPTIONAL_RUN_ARTIFACTS = [
    "build.log",
    "validation.log",
    "sample_output.json",
    "local_env.json",
    "requirements.lock",
    "uv.lock",
    "pixi.lock",
    "patch_report.json",
    "failure_classification.json",
    "retry_recommendation.json",
    "escalation.json",
    "package_gate.json",
    "verdict.json",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a JSON object.")
    return data


def same_file(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except FileNotFoundError:
        return False


def copy_artifact(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.exists() and same_file(source, dest):
        return
    shutil.copy2(source, dest)


def artifact_entry(path: str, description: str) -> dict[str, Any]:
    return {"path": path, "description": description}


def infer_model_dir(run_artifacts: Path, explicit_model_dir: str | None) -> tuple[Path, dict[str, Any]]:
    resolved_path = run_artifacts / "model_input_resolved.json"
    if not resolved_path.exists():
        raise FileNotFoundError(
            f"{resolved_path} is required; run materialize_onboard_inputs.py during LOAD_MODEL_INPUT first."
        )
    resolved = read_json(resolved_path)
    raw_model_dir = explicit_model_dir or resolved.get("model_dir")
    if not raw_model_dir:
        raise ValueError("model_dir is required, either via --model-dir or model_input_resolved.json.")
    return Path(str(raw_model_dir)).expanduser(), resolved


def build_manifest(
    *,
    model_dir: Path,
    resolved: dict[str, Any],
    copied_required: list[str],
    copied_optional: list[str],
) -> dict[str, Any]:
    required: dict[str, Any] = {
        "spec": artifact_entry("model.spec.yaml", "Model specification."),
        "model_py": artifact_entry("model.py", "SURE ModelWrapper implementation."),
        "server_py": artifact_entry("server.py", "Local serving surface."),
        "package_init": artifact_entry("__init__.py", "Import package marker."),
        "validate_py": artifact_entry("validate.py", "Model-local validation runner."),
        "config": artifact_entry("config.yaml", "Model-local runtime/server config."),
        "manifest": artifact_entry("artifacts/artifact_manifest.json", "This model artifact manifest."),
    }
    for name in copied_required:
        key = name.replace(".", "_").replace("-", "_")
        required[key] = artifact_entry(f"artifacts/{name}", f"Required /sure_onboard run artifact: {name}.")

    optional: dict[str, Any] = {}
    for name in copied_optional:
        key = name.replace(".", "_").replace("-", "_")
        optional[key] = artifact_entry(f"artifacts/{name}", f"Optional /sure_onboard run artifact: {name}.")

    return {
        "$schema": "./artifact_manifest.schema.json",
        "model_dir": str(model_dir),
        "instance_id": f"{resolved.get('model_name', model_dir.name)}-onboard",
        "timestamp": now_iso(),
        "model_id": resolved.get("model_id", ""),
        "model_name": resolved.get("model_name", model_dir.name),
        "phase": "local_onboard",
        "status": "staged",
        "artifacts": {
            "required": required,
            "conditional": {},
            "optional": optional,
        },
        "staging": {
            "source": "stage_model_artifacts.py",
            "copied_required_run_artifacts": copied_required,
            "copied_optional_run_artifacts": copied_optional,
        },
    }


def add_runtime_inventory_entries(manifest: dict[str, Any], model_dir: Path) -> None:
    optional = manifest.setdefault("artifacts", {}).setdefault("optional", {})
    runtime_paths = {
        "runtime_inventory": "artifacts/runtime_inventory.json",
        "runtime_links_manifest": "artifacts/runtime_links_manifest.json",
        "runtime_links": "artifacts/runtime_links",
    }
    for key, path in runtime_paths.items():
        if (model_dir / path).exists():
            optional[key] = artifact_entry(path, f"Runtime provenance evidence: {path}.")


def main_with_args(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    parser.add_argument("--model-dir")
    parser.add_argument(
        "--allow-missing-run-artifacts",
        action="store_true",
        help="Stage only present run artifacts. Use only for diagnostics; normal SAVE_ARTIFACTS should be strict.",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).expanduser().resolve()
    run_artifacts = run_dir / "artifacts"
    produces = Path(args.produces).expanduser().resolve()
    try:
        model_dir, resolved = infer_model_dir(run_artifacts, args.model_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"stage_model_artifacts failed: {exc}", file=sys.stderr)
        return 1

    model_artifacts = model_dir / "artifacts"
    missing_core = [name for name in CORE_FILES if not (model_dir / name).exists()]
    if missing_core:
        print(
            "stage_model_artifacts failed: model_dir is missing core local deployment files: "
            + ", ".join(missing_core),
            file=sys.stderr,
        )
        return 1

    missing_required = [name for name in REQUIRED_RUN_ARTIFACTS if not (run_artifacts / name).exists()]
    if missing_required and not args.allow_missing_run_artifacts:
        print(
            "stage_model_artifacts failed: run artifacts missing required state-machine outputs: "
            + ", ".join(missing_required),
            file=sys.stderr,
        )
        return 1

    copied_required: list[str] = []
    copied_optional: list[str] = []
    for name in REQUIRED_RUN_ARTIFACTS:
        source = run_artifacts / name
        if source.exists():
            copy_artifact(source, model_artifacts / name)
            copied_required.append(name)
    for name in OPTIONAL_RUN_ARTIFACTS:
        source = run_artifacts / name
        if source.exists():
            copy_artifact(source, model_artifacts / name)
            copied_optional.append(name)

    manifest = build_manifest(
        model_dir=model_dir,
        resolved=resolved,
        copied_required=copied_required,
        copied_optional=copied_optional,
    )
    model_manifest = model_artifacts / "artifact_manifest.json"
    model_manifest.parent.mkdir(parents=True, exist_ok=True)
    model_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        write_inventory(model_dir, produces=model_artifacts / "runtime_inventory.json", run_dir=run_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"stage_model_artifacts failed: runtime inventory generation failed: {exc}", file=sys.stderr)
        return 1
    add_runtime_inventory_entries(manifest, model_dir)
    model_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    produces.parent.mkdir(parents=True, exist_ok=True)
    produces.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        "stage_model_artifacts OK: "
        f"model_dir={model_dir}, required={len(copied_required)}, optional={len(copied_optional)}, "
        f"manifest={produces}"
    )
    return 0


def main() -> int:
    return main_with_args()


if __name__ == "__main__":
    sys.exit(main())
