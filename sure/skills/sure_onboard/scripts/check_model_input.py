#!/usr/bin/env python3
"""Gate script for LOAD_MODEL_INPUT.

Validates model_input_resolved.json before the agent starts discovery. This is
the deterministic counterpart of the /sure_onboard pre_start argument resolver:
the agent must explicitly persist the normalized MODEL_INPUT, target model dir,
and package profile so downstream units can reuse the same contract.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TASK_TYPES = {
    "asr",
    "s2tt",
    "sd",
    "ser",
    "tts",
    "vc",
    "kws",
    "slu",
    "gr",
    "speech_understanding",
    "sa-asr",
    "sa_asr",
}
DEPLOYMENT_TYPES = {"local", "api"}
PACKAGE_PROFILES = {"none", "docker-local", "docker-registry"}


def infer_repo_root(run_dir: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    if resolved.parent.name == "runs" and resolved.parent.parent.name == ".sure":
        return resolved.parent.parent.parent
    return Path.cwd().resolve()


def allowed_model_root(data: dict, run_dir: Path) -> tuple[Path, Path]:
    path_policy = data.get("path_policy")
    if isinstance(path_policy, dict):
        raw_allowed = path_policy.get("allowed_model_root")
        if raw_allowed:
            allowed_root = Path(str(raw_allowed)).expanduser().resolve()
            if allowed_root.name == "models" and allowed_root.parent.name == "sure":
                return allowed_root.parent.parent, allowed_root
            return allowed_root.parent, allowed_root
    repo_root = infer_repo_root(run_dir)
    return repo_root, (repo_root / "sure" / "models").resolve()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a JSON object.")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        print(f"model_input_resolved.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = load_json(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    missing = [
        field
        for field in ("model_id", "model_name", "model_dir", "task_type", "deployment_type", "package_profile")
        if not data.get(field)
    ]
    if not data.get("repo_url"):
        missing.append("repo_url")
    if missing:
        print("LOAD_MODEL_INPUT gate: missing required fields: " + ", ".join(missing), file=sys.stderr)
        return 1

    task_type = data.get("task_type")
    if task_type not in TASK_TYPES:
        print(f"LOAD_MODEL_INPUT gate: unsupported task_type={task_type!r}", file=sys.stderr)
        return 1

    deployment_type = data.get("deployment_type")
    if deployment_type not in DEPLOYMENT_TYPES:
        print(f"LOAD_MODEL_INPUT gate: unsupported deployment_type={deployment_type!r}", file=sys.stderr)
        return 1

    package_profile = data.get("package_profile")
    if package_profile not in PACKAGE_PROFILES:
        print(f"LOAD_MODEL_INPUT gate: unsupported package_profile={package_profile!r}", file=sys.stderr)
        return 1

    model_name = str(data.get("model_name"))
    if "/" in model_name or "\\" in model_name:
        print(
            "LOAD_MODEL_INPUT gate: model_name must be a single directory segment "
            '(for example "Qwen__Qwen3-ASR-1.7B"), not an owner/model path.',
            file=sys.stderr,
        )
        return 1

    model_dir = Path(str(data.get("model_dir"))).expanduser()
    repo_root, allowed_root = allowed_model_root(data, Path(args.run_dir))
    model_dir_abs = model_dir if model_dir.is_absolute() else repo_root / model_dir
    if model_dir_abs.exists() and model_dir_abs.is_symlink():
        print(
            "LOAD_MODEL_INPUT gate: model_dir must be a harness-owned directory, "
            f"not a whole-directory symlink: {model_dir_abs} -> {model_dir_abs.resolve()}. "
            "Replace it with a real sure/models/<model_name>/ directory and symlink only "
            "large immutable assets such as checkpoints/ or .runtime/*.",
            file=sys.stderr,
        )
        return 1
    if not is_relative_to(model_dir_abs.resolve(strict=False), allowed_root):
        print(
            "LOAD_MODEL_INPUT gate: model_dir must stay under the harness repo-level sure/models directory. "
            f"got model_dir={model_dir_abs}, allowed_root={allowed_root}. "
            "Use sure/models/<model_name>/ for generated wrappers and reference original SURE-EVAL "
            "models only through evidence fields or asset-level symlinks.",
            file=sys.stderr,
        )
        return 1
    if model_name not in model_dir.parts and model_dir.name != model_name:
        print(
            f"LOAD_MODEL_INPUT gate: model_dir should be the normalized model directory for {model_name}: {model_dir}",
            file=sys.stderr,
        )
        return 1

    model_input_path = data.get("model_input_path")
    if model_input_path:
        mip = Path(str(model_input_path)).expanduser()
        if mip.is_absolute() and not mip.exists():
            print(f"LOAD_MODEL_INPUT gate: model_input_path does not exist: {mip}", file=sys.stderr)
            return 1

    max_retries = data.get("max_retries")
    if max_retries is not None and (not isinstance(max_retries, int) or max_retries <= 0):
        print("LOAD_MODEL_INPUT gate: max_retries must be a positive integer.", file=sys.stderr)
        return 1

    print(
        "check_model_input OK: "
        f"model_id={data.get('model_id')}, model_name={model_name}, package={package_profile}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
