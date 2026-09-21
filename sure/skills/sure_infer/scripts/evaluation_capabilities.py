#!/usr/bin/env python3
"""Read route and metric capabilities from the standalone sure-evaluation engine."""

from __future__ import annotations

import json
import sys
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
    _insert_engine_src(engine_root)
    from sure_eval.evaluation.agent_plan import build_agent_plan
    from sure_eval.evaluation.cli_adapters import build_pipeline_spec

    default_plan = build_agent_plan(
        engine_task,
        language=language or None,
        include_root_env=False,
    )
    default_metrics = _dedupe([str(item) for item in default_plan.get("metrics") or []])
    route_choices: list[dict[str, Any]] = []
    supported_metrics: list[str] = []
    try:
        spec = build_pipeline_spec(engine_task, language=language or None)
    except ValueError:
        # The engine raises ValueError to say no route is configured for this
        # task/language. That is an answer -- no routes -- not a failure.
        spec = {}
    except Exception as exc:
        # Anything else (the engine's lazy task imports, a partial checkout, a
        # sure_eval package shadowing it) means the engine could not answer.
        # Reporting the defaults here is indistinguishable from a real answer.
        raise RuntimeError(
            f"sure-evaluation engine at {engine_root} could not describe task "
            f"{engine_task!r} (language={language!r}): {type(exc).__name__}: {exc}"
        ) from exc
    route_choices = [dict(item) for item in spec.get("route_choices") or [] if isinstance(item, dict)]
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
