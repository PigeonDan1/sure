#!/usr/bin/env python3
"""Preflight: verify the sure-evaluation package supports every requested route.

Reads eval_input_resolved.json and writes evaluation_preflight.json with the
verdict. Exit codes:

- 0: every requested (task, language, metric) is supported, or the engine is
  unavailable and the check is skipped (later stages report engine issues).
- 2: usage or input errors.
- 3: the evaluation package does not support a requested route. This is a
  terminal verdict — no retry can make an unsupported route runnable.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluation_capabilities import discover_engine_capabilities  # noqa: E402

PREFLIGHT_SCHEMA = "sure.harness.evaluation_preflight.v1"
REASON_CODE_SUPPORTED = "SUPPORTED"
REASON_CODE_SKIPPED = "PREFLIGHT_SKIPPED_ENGINE_UNAVAILABLE"
REASON_CODE_UNSUPPORTED = "EVALUATION_PACKAGE_UNSUPPORTED"
REASON_UNSUPPORTED = (
    "evaluation package unsupported: sure-evaluation does not support the requested evaluation route"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_resolved_input(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PreflightInputError(f"resolved input not found: {path}")
    except json.JSONDecodeError as exc:
        raise PreflightInputError(f"resolved input is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        raise PreflightInputError("resolved input must be a JSON object")
    return payload


class PreflightInputError(ValueError):
    pass


def _engine_root(payload: dict, override: str) -> Path | None:
    if override:
        return Path(override)
    engine = payload.get("evaluation", {}).get("engine")
    if isinstance(engine, dict) and engine.get("engine_root"):
        return Path(str(engine["engine_root"]))
    return None


def _check_dataset(engine_root: Path, item: dict) -> list[dict]:
    name = str(item.get("name") or item.get("dataset_id") or "")
    task = str(item.get("task") or "UNKNOWN")
    language = str(item.get("language") or "").lower()
    metrics = [str(metric) for metric in item.get("default_metrics") or []]

    def unsupported(metric: str, detail: str) -> dict:
        return {
            "dataset": name,
            "task": task,
            "language": language,
            "metric": metric,
            "supported": False,
            "detail": detail,
        }

    try:
        capabilities = discover_engine_capabilities(engine_root, task, language)
    except ValueError as exc:
        return [unsupported(metric, str(exc)) for metric in metrics or [""]]

    accepted = {str(metric).strip().lower() for metric in capabilities.get("supported_metrics") or []}
    for row in capabilities.get("catalog_entries") or []:
        if row.get("pipeline_id"):
            accepted.add(str(row["pipeline_id"]).strip().lower())
        for alias in row.get("execution_metrics") or []:
            accepted.add(str(alias).strip().lower())
    for route in capabilities.get("route_choices") or []:
        if route.get("pipeline_id"):
            accepted.add(str(route["pipeline_id"]).strip().lower())

    checks = []
    for metric in metrics:
        if metric.strip().lower() in accepted:
            checks.append(
                {
                    "dataset": name,
                    "task": task,
                    "language": language,
                    "metric": metric,
                    "supported": True,
                    "detail": "",
                }
            )
        else:
            checks.append(
                unsupported(
                    metric,
                    f"No configured route found for {task} (language={language}, metric={metric})",
                )
            )
    return checks


def build_preflight(payload: dict, engine_override: str = "") -> dict:
    engine_root = _engine_root(payload, engine_override)
    if engine_root is None or not engine_root.is_dir():
        return {
            "schema": PREFLIGHT_SCHEMA,
            "generated_at": _utc_now(),
            "supported": True,
            "reason_code": REASON_CODE_SKIPPED,
            "reason": "evaluation engine unavailable; route support preflight skipped",
            "engine": None,
            "checks": [],
        }
    engine = {"engine_root": str(engine_root)}
    datasets = payload.get("datasets")
    if not isinstance(datasets, list):
        raise PreflightInputError("resolved input datasets must be a list")
    checks = []
    for item in datasets:
        if isinstance(item, dict):
            checks.extend(_check_dataset(engine_root, item))
    supported = all(check["supported"] for check in checks)
    return {
        "schema": PREFLIGHT_SCHEMA,
        "generated_at": _utc_now(),
        "supported": supported,
        "reason_code": REASON_CODE_SUPPORTED if supported else REASON_CODE_UNSUPPORTED,
        "reason": "all requested evaluation routes are supported" if supported else REASON_UNSUPPORTED,
        "engine": engine,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight sure-evaluation route support for a resolved input")
    parser.add_argument("--input", required=True, help="Path to eval_input_resolved.json")
    parser.add_argument("--output", help="Path to write evaluation_preflight.json")
    parser.add_argument("--evaluation-engine-root", default="")
    args = parser.parse_args()

    try:
        payload = _load_resolved_input(Path(args.input))
        verdict = build_preflight(payload, args.evaluation_engine_root)
    except PreflightInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    text = json.dumps(verdict, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    if verdict["reason_code"] == REASON_CODE_UNSUPPORTED:
        print(verdict["reason"], file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
