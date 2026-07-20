#!/usr/bin/env python3
"""Resolve the standalone sure-evaluation engine used by harness evaluation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[4]


def _candidate_roots(explicit: str | None) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    if explicit:
        candidates.append(("explicit", Path(explicit).expanduser()))
    env_home = os.environ.get("SURE_EVALUATION_HOME")
    if env_home:
        candidates.append(("SURE_EVALUATION_HOME", Path(env_home).expanduser()))
    repo_root = _repo_root_from_script()
    candidates.append(("submodule", repo_root / "sure" / "external" / "sure-evaluation"))

    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for source, path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append((source, path))
    return out


def _is_engine_root(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "pyproject.toml").exists()
        and (path / "src" / "sure_eval" / "evaluation").is_dir()
    )


def _resolve(explicit: str | None) -> tuple[str, Path] | None:
    for source, path in _candidate_roots(explicit):
        if _is_engine_root(path):
            return source, path.resolve()
    return None


def resolve_engine_root(explicit: str | None = None) -> tuple[str, Path] | None:
    """Return the selected sure-evaluation engine root, if one is available."""

    return _resolve(explicit)


def _git_info(root: Path) -> dict[str, Any]:
    git_dir = root / ".git"
    if not git_dir.exists():
        return {"is_git": False}
    result: dict[str, Any] = {"is_git": True}
    try:
        subprocess.run(["git", "--version"], cwd=root, capture_output=True, text=True, check=False)
    except OSError as exc:
        return {"is_git": True, "git_available": False, "error": str(exc)}
    result["git_available"] = True
    for key, command in {
        "commit": ["git", "rev-parse", "HEAD"],
        "branch": ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    }.items():
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        result[key] = completed.stdout.strip() if completed.returncode == 0 else None
    dirty = subprocess.run(["git", "status", "--short"], cwd=root, capture_output=True, text=True, check=False)
    result["dirty"] = bool(dirty.stdout.strip()) if dirty.returncode == 0 else None
    return result


def _smoke_describe(root: Path, task: str, language: str, metric: str) -> dict[str, Any]:
    env = os.environ.copy()
    src = str(root / "src")
    env["PYTHONPATH"] = f"{src}:{env.get('PYTHONPATH', '')}" if env.get("PYTHONPATH") else src
    code = """
import json
from sure_eval.evaluation.cli_adapters import build_pipeline_spec
payload = build_pipeline_spec(__TASK__, language=__LANGUAGE__, metric=__METRIC__)
print(json.dumps(payload, ensure_ascii=False))
""".replace("__TASK__", repr(task)).replace("__LANGUAGE__", repr(language)).replace("__METRIC__", repr(metric))
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        return {
            "ok": False,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "error": f"invalid JSON smoke output: {exc}",
        }
    return {
        "ok": True,
        "returncode": completed.returncode,
        "pipeline_id": payload.get("pipeline_id"),
        "task": payload.get("task"),
        "language": payload.get("language"),
        "metric": payload.get("metric"),
        "nodes": [node.get("node_id") for node in payload.get("nodes", [])],
        "payload": payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve standalone sure-evaluation engine")
    parser.add_argument("--engine-root", help="Explicit sure-evaluation repository root")
    parser.add_argument("--smoke", action="store_true", help="Run a lightweight pipeline describe smoke")
    parser.add_argument("--task", default="asr")
    parser.add_argument("--language", default="en")
    parser.add_argument("--metric", default="wer")
    parser.add_argument("--output")
    args = parser.parse_args()

    resolved = _resolve(args.engine_root)
    report: dict[str, Any] = {
        "schema": "sure.harness_evaluation_engine.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": resolved is not None,
        "source": resolved[0] if resolved else None,
        "engine_root": str(resolved[1]) if resolved else None,
        "candidates": [{"source": source, "path": str(path)} for source, path in _candidate_roots(args.engine_root)],
        "checks": {},
        "git": None,
        "smoke": None,
    }
    if resolved is not None:
        _, root = resolved
        report["checks"] = {
            "pyproject": (root / "pyproject.toml").exists(),
            "pipeline_catalog_jsonl": (root / "docs" / "pipeline_catalog.jsonl").exists(),
            "evaluation_package": (root / "src" / "sure_eval" / "evaluation").is_dir(),
            "task_manifests": (root / "src" / "sure_eval" / "evaluation" / "tasks").is_dir(),
            "node_manifests": (root / "src" / "sure_eval" / "evaluation" / "nodes").is_dir(),
        }
        report["git"] = _git_info(root)
        if args.smoke:
            report["smoke"] = _smoke_describe(root, args.task, args.language, args.metric)
            report["ok"] = bool(report["smoke"].get("ok"))

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
