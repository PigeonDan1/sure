#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xforge_sure_bridge.modelscope_daily import SUPPORTED_TASKS, build_daily_summary, write_daily_summary
from xforge_sure_bridge.modelscope_watcher import ModelScopeWatcher


def _today_string() -> str:
    return datetime.now().date().isoformat()


def _load_candidates(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        value = data.get("candidates") or data.get("items") or data.get("data")
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    raise ValueError(f"cannot load candidates from {path}")


def _matches_task(candidate: dict[str, Any], task: str) -> bool:
    candidate_task = str(candidate.get("task") or "").lower()
    resource_id = str(candidate.get("resource_id") or "").lower()
    name = str(candidate.get("name") or "").lower()
    return task.lower() in " ".join((candidate_task, resource_id, name))


def _offline_candidates_by_task(candidates: list[dict[str, Any]], tasks: list[str]) -> dict[str, list[dict[str, Any]]]:
    return {task: [candidate for candidate in candidates if _matches_task(candidate, task)] for task in tasks}


def _online_candidates_by_task(
    tasks: list[str],
    resource_types: list[str],
    since_days: int,
    max_items: int,
    api_base: str | None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    watcher = ModelScopeWatcher(api_base=api_base) if api_base else ModelScopeWatcher()
    candidates_by_task: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, Any]] = []
    for task in tasks:
        task_candidates: list[dict[str, Any]] = []
        for resource_type in resource_types:
            try:
                task_candidates.extend(
                    watcher.search(
                        task=task,
                        resource_types=[resource_type],
                        since_days=since_days,
                        max_items=max_items,
                    )
                )
            except Exception as exc:
                errors.append({"task": task, "resource_type": resource_type, "error": str(exc)})
        candidates_by_task[task] = task_candidates
    return candidates_by_task, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Write daily ModelScope model/dataset summaries for human review")
    parser.add_argument("--tasks", nargs="+", default=list(SUPPORTED_TASKS), choices=SUPPORTED_TASKS)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--date", default="today")
    parser.add_argument("--output-root", default="reports/xforge/modelscope")
    parser.add_argument("--candidates-json", help="Use offline candidate JSON instead of querying ModelScope")
    parser.add_argument("--since-days", type=int, default=1)
    parser.add_argument("--max-items", type=int, default=50)
    parser.add_argument("--resource", choices=["model", "dataset", "all"], default="all")
    parser.add_argument("--api-base", default=None)
    args = parser.parse_args()

    report_date = _today_string() if args.date == "today" else args.date
    resource_types = ["model", "dataset"] if args.resource == "all" else [args.resource]

    try:
        if args.candidates_json:
            candidates = _load_candidates(Path(args.candidates_json))
            candidates_by_task = _offline_candidates_by_task(candidates, list(args.tasks))
            errors: list[dict[str, Any]] = []
        else:
            candidates_by_task, errors = _online_candidates_by_task(
                tasks=list(args.tasks),
                resource_types=resource_types,
                since_days=args.since_days,
                max_items=args.max_items,
                api_base=args.api_base,
            )

        summary = build_daily_summary(
            candidates_by_task=candidates_by_task,
            errors=errors,
            report_date=report_date,
            top_k=args.top_k,
        )
        output = write_daily_summary(summary, args.output_root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
