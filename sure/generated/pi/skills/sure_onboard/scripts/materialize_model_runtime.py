#!/usr/bin/env python3
"""Seal a local package=none build environment into the site Model Runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from sure.runtime.model.bootstrap import ModelRuntimeError, materialize_runtime
from sure.site.loader import SitePolicyError, load_site_policy


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return value


def repo_root_for(run_dir: Path) -> Path:
    if run_dir.parent.name == "runs" and run_dir.parent.parent.name == ".sure":
        return run_dir.parent.parent.parent
    return Path.cwd().resolve()


def resolve_model_path(raw: object, model_dir: Path, repo_root: Path) -> Path:
    path = Path(str(raw or "")).expanduser()
    candidates = [path] if path.is_absolute() else [model_dir / path, repo_root / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def materialize(run_dir: Path, draft_path: Path, produces: Path) -> dict[str, Any]:
    repo_root = repo_root_for(run_dir)
    resolved = read_json(run_dir / "artifacts" / "model_input_resolved.json")
    if resolved.get("deployment_type") != "local" or resolved.get("package_profile") != "none":
        raise ValueError("materialize_model_runtime.py is only for local package=none onboarding")
    policy = load_site_policy(repository_root=repo_root, required=True)
    assert policy is not None
    if "local" not in policy["policy"]["execution"]["surfaces"]:
        raise ValueError("local Python runtimes require local in execution.surfaces")
    if "python" not in policy["policy"]["execution"]["local_runtimes"]:
        raise ValueError("local Python runtimes are disabled by execution.local_runtimes")

    draft = read_json(draft_path)
    if draft.get("backend") != "uv":
        raise ValueError("local package=none initially supports backend=uv only")
    if draft.get("env_ready") is not True:
        raise ValueError("build environment draft must declare env_ready=true")
    model_dir = Path(str(draft.get("model_dir") or resolved.get("model_dir") or "")).expanduser().resolve()
    if not model_dir.is_dir():
        raise ValueError(f"model_dir does not exist: {model_dir}")
    source_python = resolve_model_path(draft.get("python_executable"), model_dir, repo_root)
    lock_path = resolve_model_path(draft.get("lockfile_path"), model_dir, repo_root)
    try:
        lock_relative = str(lock_path.relative_to(model_dir))
    except ValueError as exc:
        raise ValueError("package=none lockfile_path must be inside model_dir for promotion") from exc

    contract = materialize_runtime(
        runtime_root=Path(policy["policy"]["storage"]["runtime_root"]) / "models",
        source_python=source_python,
        lock_path=lock_path,
    )
    manifest = {
        key: value
        for key, value in contract.items()
        if key
        not in {
            "runtime_root",
            "manifest_path",
            "python_executable_resolved",
            "manifest_sha256",
            "probe",
        }
    }
    manifest_path = run_dir / "artifacts" / "model_runtime_manifest.json"
    atomic_json(manifest_path, manifest)
    result = dict(draft)
    result.update(
        {
            "model_dir": str(model_dir),
            "python_executable": contract["python_executable_resolved"],
            "python_version": contract["python_version"],
            "lockfile_path": lock_relative,
            "model_runtime": {
                "schema": manifest["schema"],
                "runtime_id": manifest["runtime_id"],
                "backend": manifest["backend"],
                "python_executable": manifest["python_executable"],
                "manifest_sha256": contract["manifest_sha256"],
                "lock_sha256": manifest["lock_sha256"],
            },
            "runtime_probe": contract["probe"],
        }
    )
    atomic_json(produces, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--produces", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = materialize(
            args.run_dir.expanduser().resolve(),
            args.input.expanduser().resolve(),
            args.produces.expanduser().resolve(),
        )
    except (OSError, ValueError, ModelRuntimeError, SitePolicyError) as exc:
        print(f"materialize_model_runtime failed: {exc}", file=sys.stderr)
        return 1
    print(
        "materialize_model_runtime OK: "
        f"runtime_id={result['model_runtime']['runtime_id']}, backend={result['backend']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
