#!/usr/bin/env python3
"""Validate the terminal /sure_reval report artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REQUIRED_ARTIFACTS = [
    "prediction_source_resolved",
    "prediction_reuse_manifest",
    "evaluation_route_plan",
    "validation_payload",
    "evaluation_payload",
    "protocol",
    "report_jsonl",
    "report_snapshot",
    "predictions_dir",
    "metrics_dir",
    "sample_reports_dir",
    "model_eval_manifest",
    "main_agent_run_report",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    path = Path(args.report)
    if not path.is_file():
        print(f"reval report not found: {path}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"invalid JSON: {exc}", file=sys.stderr)
        return 1
    if payload.get("schema") != "sure.reval.run_report.v1":
        print("schema must be sure.reval.run_report.v1", file=sys.stderr)
        return 1
    if payload.get("evaluation_only") is not True:
        print("evaluation_only must be true", file=sys.stderr)
        return 1
    if payload.get("old_evaluation_reused") is not False:
        print("old_evaluation_reused must be false", file=sys.stderr)
        return 1
    run_dir = Path(str(payload.get("run_dir") or ""))
    if not run_dir.is_dir():
        print(f"run_dir does not exist: {run_dir}", file=sys.stderr)
        return 1
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    missing_keys = [key for key in REQUIRED_ARTIFACTS if not artifacts.get(key)]
    if missing_keys:
        print("missing artifact keys: " + ", ".join(missing_keys), file=sys.stderr)
        return 1
    missing_paths = []
    for key in REQUIRED_ARTIFACTS:
        artifact = Path(str(artifacts[key]))
        if not artifact.exists():
            missing_paths.append(f"{key}={artifact}")
    if missing_paths:
        print("missing artifact paths:\n  - " + "\n  - ".join(missing_paths), file=sys.stderr)
        return 1
    print(f"reval_run_report OK: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
