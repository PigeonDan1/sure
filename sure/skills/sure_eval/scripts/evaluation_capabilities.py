#!/usr/bin/env python3
"""Read route and metric capabilities from the standalone sure-evaluation engine."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


TASK_TO_ENGINE_TASK = {
    "ASR": "asr",
    "S2TT": "s2tt",
    "SD": "sd",
    "SA-ASR": "sa-asr",
    "SA_ASR": "sa-asr",
    "KWS": "kws",
    "SER": "ser",
    "GR": "gr",
    "CLASSIFICATION": "classification",
    "SLU": "slu",
    "SPEECH_UNDERSTANDING": "slu",
    "TTS": "tts",
    "VC": "vc",
}


def normalize_engine_task(task: str) -> str:
    """Map harness task labels to the engine task id."""

    normalized = task.strip().upper().replace(" ", "_")
    if normalized not in TASK_TO_ENGINE_TASK:
        raise ValueError(f"Unsupported evaluation task for sure-evaluation: {task!r}")
    return TASK_TO_ENGINE_TASK[normalized]


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


def discover_engine_capabilities(engine_root: Path, task: str, language: str) -> dict[str, Any]:
    """Return current engine-supported metrics and route choices for a task/language."""

    engine_task = normalize_engine_task(task)
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
        route_choices = [dict(item) for item in spec.get("route_choices") or [] if isinstance(item, dict)]
        for route in route_choices:
            metric = str(route.get("metric") or "").strip()
            if not metric:
                continue
            route_language = route.get("language")
            if route_language in (None, "", language):
                supported_metrics.append(metric)
    except Exception:
        route_choices = []

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
