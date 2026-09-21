#!/usr/bin/env python3
"""Read route and metric capabilities from the standalone sure-evaluation engine."""

from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "runtime" / "evaluation" / "task_registry.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.runtime.evaluation.task_registry import normalize_task, task_profile


STATIC_CAPABILITIES = (
    Path(__file__).resolve().parents[4]
    / "sure"
    / "runtime"
    / "evaluation"
    / "engine-capabilities.generated.json"
)


def normalize_engine_task(task: str) -> str:
    """Map harness task labels to the engine task id."""

    normalized = normalize_task(task)
    if normalized == "speech_understanding":
        raise ValueError("speech_understanding is a suite; evaluate its atomic tasks separately")
    try:
        task_profile(normalized)
    except ValueError as exc:
        raise ValueError(f"Unsupported evaluation task for sure-evaluation: {task!r}") from exc
    return "sa-asr" if normalized == "sa_asr" else normalized


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in out:
            out.append(item)
    return out


def _insert_engine_src(engine_root: Path) -> None:
    src = str(engine_root / "src")
    if src in sys.path:
        sys.path.remove(src)
    sys.path.insert(0, src)


def _probe_engine_here(engine_root: str, engine_task: str, language: str) -> dict[str, Any]:
    """Ask the engine about one task/language, importing it into this process.

    Only the child process started by _probe_engine() may call this: it pins
    the name `sure_eval` to the engine's package for the rest of the process.
    """

    _insert_engine_src(Path(engine_root))
    try:
        from sure_eval.evaluation.agent_plan import build_agent_plan
        from sure_eval.evaluation.cli_adapters import build_pipeline_spec

        plan = build_agent_plan(engine_task, language=language or None, include_root_env=False)
    except ValueError as exc:
        # The engine rejects the task or language itself (an unsupported ASR
        # language, say). Both callers read ValueError as "not covered here"
        # and fall back, so it must stay a ValueError in the parent.
        return {"status": "unsupported", "message": str(exc)}
    default_metrics = [str(item) for item in plan.get("metrics") or []]
    try:
        spec = build_pipeline_spec(engine_task, language=language or None)
    except ValueError:
        # The engine raises ValueError to say no route is configured for this
        # task/language. That is an answer -- no routes -- not a failure.
        return {"status": "no_route", "default_metrics": default_metrics}
    route_choices = [item for item in spec.get("route_choices") or [] if isinstance(item, dict)]
    return {"status": "ok", "default_metrics": default_metrics, "route_choices": route_choices}


@lru_cache(maxsize=None)
def _probe_engine(engine_root: str, engine_task: str, language: str) -> dict[str, Any]:
    """Run one capability probe in a child process, and remember its answer.

    The engine's src/ carries a package named sure_eval, the same top-level
    name as the harness-local package next to this file, and the two are
    divergent forks of it. Whichever is imported first pins the name for the
    whole process, and because both ship the same modules the loser's imports
    keep working and silently hand back the other fork's classes. Importing
    the engine only in a child keeps it out of every harness process.
    """

    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), engine_root, engine_task, language],
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    try:
        # The engine may log to stdout; the probe's answer is the last line.
        answer = json.loads(stdout.rsplit("\n", 1)[-1])
    except json.JSONDecodeError:
        answer = None
    if not isinstance(answer, dict) or "status" not in answer:
        return {
            "status": "error",
            "type": "EngineProbeFailed",
            "message": f"probe exited {completed.returncode} without an answer: {stderr or stdout}",
        }
    return answer


def _catalog_entries(engine_root: Path, task: str, language: str) -> list[dict[str, Any]]:
    catalog_path = engine_root / "docs" / "pipeline_catalog.jsonl"
    if not catalog_path.is_file():
        return []
    engine_task = normalize_engine_task(task)
    rows: list[dict[str, Any]] = []
    for line in catalog_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        aliases = {
            str(row.get("task") or "").strip().lower().replace("-", "_"),
            str(row.get("task_alias") or "").strip().lower().replace("-", "_"),
        }
        if engine_task.replace("-", "_") not in aliases:
            continue
        row_language = str(row.get("language") or "")
        if row_language and language and row_language != language:
            continue
        rows.append(row)
    return rows


def _static_capabilities(task: str, language: str) -> dict[str, Any] | None:
    """Read the checked-in capability snapshot when the engine checkout is absent."""
    if not STATIC_CAPABILITIES.is_file():
        return None
    try:
        document = json.loads(STATIC_CAPABILITIES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tasks = document.get("tasks")
    if not isinstance(tasks, dict):
        return None
    profile = tasks.get(task)
    if not isinstance(profile, dict):
        return None
    language_name = str(language or "").strip().lower()
    routes = [route for route in profile.get("routes") or [] if isinstance(route, dict)]
    matching = [
        route
        for route in routes
        if not language_name or str(route.get("language") or "").strip().lower() in {"", language_name}
    ]
    catalog = [{"task": task, **route} for route in matching]
    metrics = _dedupe([str(route.get("metric") or "") for route in matching])
    return {
        "task": str(profile.get("engine_task") or task),
        "language": language,
        "default_metrics": metrics[:1],
        "supported_metrics": metrics,
        "route_choices": matching,
        "catalog_entries": catalog,
    }


def discover_engine_capabilities(engine_root: Path, task: str, language: str) -> dict[str, Any]:
    """Return current engine-supported metrics and route choices for a task/language."""

    engine_task = normalize_engine_task(task)
    if not (engine_root / "src" / "sure_eval" / "evaluation" / "agent_plan.py").is_file():
        static = _static_capabilities(engine_task, language)
        if static is not None:
            return static
    answer = _probe_engine(str(engine_root), engine_task, language)
    status = answer.get("status")
    if status == "unsupported":
        raise ValueError(str(answer.get("message") or ""))
    if status not in {"ok", "no_route"}:
        # Anything else (the engine's lazy task imports, a partial checkout, a
        # child that died) means the engine could not answer. Reporting the
        # defaults here is indistinguishable from a real answer.
        raise RuntimeError(
            f"sure-evaluation engine at {engine_root} could not describe task "
            f"{engine_task!r} (language={language!r}): "
            f"{answer.get('type')}: {answer.get('message')}"
        )
    default_metrics = _dedupe([str(item) for item in answer.get("default_metrics") or []])
    supported_metrics: list[str] = []
    route_choices = [dict(item) for item in answer.get("route_choices") or [] if isinstance(item, dict)]
    for route in route_choices:
        metric = str(route.get("metric") or "").strip()
        if not metric:
            continue
        route_language = route.get("language")
        if route_language in (None, "", language):
            supported_metrics.append(metric)

    for row in _catalog_entries(engine_root, task, language):
        metric = str(row.get("metric") or "").strip()
        if metric:
            supported_metrics.append(metric)

    return {
        "task": engine_task,
        "language": language,
        "default_metrics": default_metrics,
        "supported_metrics": _dedupe(supported_metrics or default_metrics),
        "route_choices": route_choices,
        "catalog_entries": _catalog_entries(engine_root, task, language),
    }


def default_metrics_for_task_language(engine_root: Path, task: str, language: str) -> list[str]:
    """Return the engine default metrics for a task/language."""

    return list(discover_engine_capabilities(engine_root, task, language).get("default_metrics") or [])


def supported_metrics_for_task_language(engine_root: Path, task: str, language: str) -> list[str]:
    """Return the engine-supported metrics for a task/language."""

    return list(discover_engine_capabilities(engine_root, task, language).get("supported_metrics") or [])


def _probe_main(argv: list[str]) -> int:
    """Child entry point for _probe_engine(): one JSON answer on the last stdout line."""

    try:
        answer = _probe_engine_here(*argv)
    except Exception as exc:  # the parent turns this into its RuntimeError
        answer = {"status": "error", "type": type(exc).__name__, "message": str(exc)}
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_probe_main(sys.argv[1:]))
