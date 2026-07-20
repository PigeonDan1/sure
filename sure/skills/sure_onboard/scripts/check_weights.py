#!/usr/bin/env python3
"""Gate script for the FETCH_WEIGHTS unit.

Verifies weights_ready is true and (when weights.required) the resolved local
model path exists. Enforces the model-local-first checkpoint rule: a fallback
to host-global paths must carry a reason.
Called by the Sure hook:
    python3 scripts/check_weights.py --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SUCCESS_STATUSES = {"fetched", "passed", "success", "ready"}
NO_CHECKPOINT_SOURCES = {"api", "pip", "release_or_pypi", "none"}


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a JSON object.")
    return data


def get_nested(data: dict, *keys: str) -> object | None:
    current: object = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def load_resolved(run_dir: Path) -> dict:
    path = run_dir / "artifacts" / "model_input_resolved.json"
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except ValueError:
        return {}


def truthy_success(data: dict) -> bool:
    if data.get("weights_ready") is True:
        return True
    status = data.get("status")
    return isinstance(status, str) and status.lower() in SUCCESS_STATUSES


def weights_required(data: dict, resolved_input: dict) -> bool:
    if isinstance(data.get("required"), bool):
        return bool(data["required"])
    if isinstance(data.get("required_for_phase1"), bool):
        return bool(data["required_for_phase1"])
    value = get_nested(resolved_input, "normalized_model_input", "weights", "required")
    return bool(value) if isinstance(value, bool) else True


def model_dir_from(data: dict, resolved_input: dict) -> Path | None:
    raw = data.get("model_dir") or resolved_input.get("model_dir")
    return Path(str(raw)).expanduser() if raw else None


def repo_root_for(run_dir: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    if resolved.parent.name == "runs" and resolved.parent.parent.name == ".sure":
        return resolved.parent.parent.parent
    return Path.cwd().resolve()


def normalize_model_dir(model_dir: Path | None, repo_root: Path) -> Path | None:
    if model_dir is None:
        return None
    model_dir = model_dir.expanduser()
    return model_dir if model_dir.is_absolute() else repo_root / model_dir


def resolve_declared_path(
    raw: object,
    *,
    model_dir: Path | None,
    manifest_path: Path,
    run_dir: Path,
    repo_root: Path,
) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path
    candidates = []
    if len(path.parts) >= 2 and path.parts[0] == "sure" and path.parts[1] == "models":
        candidates.append(repo_root / path)
    if model_dir:
        candidates.append(model_dir / path)
    candidates.append(repo_root / path)
    candidates.append(run_dir / "artifacts" / path)
    candidates.append(run_dir / path)
    candidates.append(manifest_path.parent / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else path


def lexical_under(path: Path, root: Path) -> bool:
    try:
        path.absolute().relative_to(root.absolute())
        return True
    except ValueError:
        return False


def dependency_errors(
    data: dict,
    *,
    model_dir: Path | None,
    manifest_path: Path,
    run_dir: Path,
    repo_root: Path,
) -> list[str]:
    errors: list[str] = []
    dependencies = data.get("dependencies")
    if isinstance(dependencies, list):
        for idx, dep in enumerate(dependencies):
            if not isinstance(dep, dict):
                continue
            local_path = dep.get("local_path")
            exists_flag = dep.get("exists")
            resolved_local_path = resolve_declared_path(
                local_path,
                model_dir=model_dir,
                manifest_path=manifest_path,
                run_dir=run_dir,
                repo_root=repo_root,
            )
            if local_path and (not resolved_local_path or not resolved_local_path.exists()):
                errors.append(f"dependencies[{idx}].local_path does not exist: {local_path}")
            if exists_flag is False:
                errors.append(f"dependencies[{idx}].exists is false")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    path = Path(args.produces).expanduser().resolve()
    if not path.exists():
        print(f"weights_manifest.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = read_json(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not truthy_success(data):
        print(
            "FETCH_WEIGHTS gate failed: weights manifest must have weights_ready=true "
            "or status=fetched/passed/success/ready.",
            file=sys.stderr,
        )
        return 1

    run_dir = Path(args.run_dir).expanduser().resolve()
    repo_root = repo_root_for(run_dir)
    resolved_input = load_resolved(run_dir)
    model_dir = normalize_model_dir(model_dir_from(data, resolved_input), repo_root)
    required = weights_required(data, resolved_input)
    source = str(data.get("source") or get_nested(resolved_input, "normalized_model_input", "weights", "source") or "")

    resolved = data.get("resolved_local_model_path")
    resolved_path = resolve_declared_path(
        resolved,
        model_dir=model_dir,
        manifest_path=path,
        run_dir=run_dir,
        repo_root=repo_root,
    )
    if required and source not in NO_CHECKPOINT_SOURCES and not resolved_path:
        print(
            "FETCH_WEIGHTS gate: weights are required but resolved_local_model_path is missing.",
            file=sys.stderr,
        )
        return 1
    if resolved_path and not resolved_path.exists():
        print(
            f"FETCH_WEIGHTS gate: resolved_local_model_path does not exist: {resolved}",
            file=sys.stderr,
        )
        return 1

    for field in ("cache_dir", "checkpoint_root", "hf_cache_root", "modelscope_cache_root"):
        declared = data.get(field)
        if declared:
            resolved_declared = resolve_declared_path(
                declared,
                model_dir=model_dir,
                manifest_path=path,
                run_dir=run_dir,
                repo_root=repo_root,
            )
            if not resolved_declared or not resolved_declared.exists():
                print(f"FETCH_WEIGHTS gate: declared {field} does not exist: {declared}", file=sys.stderr)
                return 1

    dep_errors = dependency_errors(
        data,
        model_dir=model_dir,
        manifest_path=path,
        run_dir=run_dir,
        repo_root=repo_root,
    )
    if dep_errors:
        print("FETCH_WEIGHTS gate: " + "; ".join(dep_errors[:6]), file=sys.stderr)
        return 1

    outside_model_dir = bool(resolved_path and model_dir and not lexical_under(resolved_path, model_dir))
    if outside_model_dir and not data.get("fallback_to_host_global"):
        print(
            "FETCH_WEIGHTS gate: resolved_local_model_path is outside model_dir. "
            "Use a model-local path/symlink, or set fallback_to_host_global=true with fallback_reason.",
            file=sys.stderr,
        )
        return 1

    if data.get("fallback_to_host_global"):
        reason = data.get("fallback_reason", "")
        if not reason:
            print(
                "FETCH_WEIGHTS gate: fallback_to_host_global is true but no "
                "fallback_reason recorded. Document why weights could not be "
                "resolved model-local.",
                file=sys.stderr,
            )
            return 1

    print(f"check_weights OK: weights_ready=true, source={source or data.get('source')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
