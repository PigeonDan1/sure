#!/usr/bin/env python3
"""Resolve an onboarded SURE model directory and emit a static readiness report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _repo_root_from_script() -> Path:
    # scripts/resolve_model_dir.py -> sure_eval -> skills -> sure -> repo root
    return Path(__file__).resolve().parents[4]


def _legacy_models_dir(root: str | Path) -> Path:
    path = Path(root).expanduser()
    if path.name == "models":
        return path
    return path / "src" / "sure_eval" / "models"


def _candidate_model_dirs(model: str, explicit: str | None, cwd: Path) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    if explicit:
        candidates.append(("explicit_model_dir", Path(explicit).expanduser()))

    for key in ("SURE_MODELS_DIR", "SURE_MODEL_ROOT"):
        value = os.environ.get(key)
        if value:
            candidates.append((key, Path(value).expanduser() / model))

    value = os.environ.get("LEGACY_SURE_MODELS_DIR")
    if value:
        candidates.append(("LEGACY_SURE_MODELS_DIR", Path(value).expanduser() / model))

    value = os.environ.get("LEGACY_SURE_EVAL_ROOT")
    if value:
        candidates.append(("LEGACY_SURE_EVAL_ROOT", _legacy_models_dir(value) / model))

    candidates.append(("cwd_sure_models", cwd / "sure" / "models" / model))
    candidates.append(("repo_sure_models", _repo_root_from_script() / "sure" / "models" / model))

    deduped: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for source, path in candidates:
        resolved = path.resolve() if path.exists() else path
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append((source, path))
    return deduped


def _first_existing(candidates: list[tuple[str, Path]]) -> tuple[str, Path] | None:
    for source, path in candidates:
        if path.is_dir():
            return source, path
    return None


def _verdict_path(model_dir: Path) -> Path | None:
    for path in (model_dir / "verdict.json", model_dir / "artifacts" / "verdict.json"):
        if path.exists():
            return path
    return None


def _checks(model_dir: Path | None) -> dict[str, Any]:
    if model_dir is None:
        return {
            "model_dir_exists": False,
            "config_yaml": False,
            "model_spec_yaml": False,
            "model_py": False,
            "server_py": False,
            "verdict_json": False,
            "artifacts_verdict_json": False,
        }
    return {
        "model_dir_exists": model_dir.is_dir(),
        "config_yaml": (model_dir / "config.yaml").exists(),
        "model_spec_yaml": (model_dir / "model.spec.yaml").exists(),
        "model_py": (model_dir / "model.py").exists(),
        "server_py": (model_dir / "server.py").exists(),
        "verdict_json": (model_dir / "verdict.json").exists(),
        "artifacts_verdict_json": (model_dir / "artifacts" / "verdict.json").exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a SURE model directory")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-dir")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--require-verdict", action="store_true")
    parser.add_argument("--require-runtime-files", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    candidates = _candidate_model_dirs(args.model, args.model_dir, Path(args.cwd).expanduser())
    selected = _first_existing(candidates)
    source: str | None = None
    model_dir: Path | None = None
    if selected is not None:
        source, model_dir = selected
        model_dir = model_dir.resolve()

    verdict = _verdict_path(model_dir) if model_dir is not None else None
    checks = _checks(model_dir)
    runtime_ready = checks["config_yaml"] and checks["model_py"] and checks["server_py"]
    verdict_ready = verdict is not None
    ok = model_dir is not None
    if args.require_runtime_files:
        ok = ok and runtime_ready
    if args.require_verdict:
        ok = ok and verdict_ready

    report = {
        "model": args.model,
        "ok": ok,
        "source": source,
        "model_dir": str(model_dir) if model_dir is not None else None,
        "verdict_path": str(verdict.resolve()) if verdict is not None else None,
        "runtime_ready_static": runtime_ready,
        "verdict_ready": verdict_ready,
        "checks": checks,
        "candidates": [{"source": item_source, "path": str(path)} for item_source, path in candidates],
    }

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)

    if ok:
        return 0
    print(f"model directory is not ready: {args.model}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
