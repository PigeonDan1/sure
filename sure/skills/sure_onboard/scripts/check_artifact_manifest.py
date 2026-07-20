#!/usr/bin/env python3
"""Gate script for SAVE_ARTIFACTS.

Checks that artifact_manifest.json indexes a real model directory and that the
core local deployment files exist. The manifest may use either the harness
grouped shape (artifacts.required.<name>.path) or the older flat upstream shape.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CORE_FILES = ["model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml"]


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a JSON object.")
    return data


def resolve_path(raw: str, model_dir: Path, manifest_dir: Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    if raw.startswith("artifacts/"):
        return model_dir / raw
    candidate = model_dir / raw
    if candidate.exists():
        return candidate
    return manifest_dir / raw


def infer_model_dir(data: dict[str, Any], manifest_path: Path) -> Path | None:
    raw = data.get("model_dir") or data.get("model_root")
    if raw:
        return Path(str(raw)).expanduser()
    if manifest_path.parent.name == "artifacts":
        candidate = manifest_path.parent.parent
        if all((candidate / name).exists() for name in CORE_FILES):
            return candidate
    return None


def collect_manifest_paths(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
        return
    if isinstance(node, dict):
        entry_type = str(node.get("type", "")).lower()
        entry_status = str(node.get("status", "")).lower()
        if entry_type in {"optional", "conditional"} and entry_status in {"absent", "missing", "skipped"}:
            return
        path = node.get("path")
        if isinstance(path, str):
            out.append(path)
        paths = node.get("paths")
        if isinstance(paths, list):
            out.extend(item for item in paths if isinstance(item, str))
        for value in node.values():
            if isinstance(value, (dict, list)):
                collect_manifest_paths(value, out)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, (dict, list)):
                collect_manifest_paths(item, out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        print(f"artifact_manifest.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = load_json(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    model_dir = infer_model_dir(data, path)
    if not model_dir:
        print(
            "SAVE_ARTIFACTS gate: model_dir is required unless artifact_manifest.json "
            "is located under <model_dir>/artifacts/ and the core files can be inferred.",
            file=sys.stderr,
        )
        return 1
    if not model_dir.exists() or not model_dir.is_dir():
        print(f"SAVE_ARTIFACTS gate: model_dir does not exist or is not a directory: {model_dir}", file=sys.stderr)
        return 1

    missing_core = [name for name in CORE_FILES if not (model_dir / name).exists()]
    if missing_core:
        print(
            "SAVE_ARTIFACTS gate: model_dir is missing core local deployment files: "
            + ", ".join(missing_core),
            file=sys.stderr,
        )
        return 1

    declared_paths: list[str] = []
    artifacts = data.get("artifacts")
    if isinstance(artifacts, dict):
        required_artifacts = artifacts.get("required") if isinstance(artifacts.get("required"), dict) else artifacts
        collect_manifest_paths(required_artifacts, declared_paths)
    elif isinstance(artifacts, list):
        collect_manifest_paths(artifacts, declared_paths)
    elif isinstance(data.get("required_artifacts"), dict):
        collect_manifest_paths(data["required_artifacts"], declared_paths)
    else:
        print(
            "SAVE_ARTIFACTS gate: manifest must declare artifacts, required_artifacts, "
            "or use the preferred artifacts.required shape.",
            file=sys.stderr,
        )
        return 1
    if not declared_paths:
        print("SAVE_ARTIFACTS gate: manifest did not declare any artifact paths.", file=sys.stderr)
        return 1
    bad_paths: list[str] = []
    for raw in declared_paths:
        resolved = resolve_path(raw, model_dir, path.parent)
        if not resolved.exists():
            bad_paths.append(raw)
    if bad_paths:
        print(
            "SAVE_ARTIFACTS gate: manifest declares paths that do not exist: "
            + ", ".join(bad_paths[:8]),
            file=sys.stderr,
        )
        return 1

    print(f"check_artifact_manifest OK: model_dir={model_dir}, declared_paths={len(declared_paths)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
