#!/usr/bin/env python3
"""Gate script for the RUN_REPORT_UNIT.

Verifies the final report is persisted, execution_path_actual is declared, and
any non-vc_submit path carries an approved fallback reason. Called by the Sure
hook:
    python3 scripts/check_run_report.py --run-dir <runDir> --produces <abs>

exit 0 = pass; non-zero = fail (stderr carries the repair text).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to main_agent_run_report.json")
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        print(f"main_agent_run_report.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"main_agent_run_report.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if not data.get("report_persisted"):
        print(
            "RUN_REPORT_UNIT gate: report_persisted must be true. Preview the report, "
            "obtain user confirmation, then persist it.",
            file=sys.stderr,
        )
        return 1

    execution_path_actual = data.get("execution_path_actual", "")
    if not execution_path_actual:
        print(
            "RUN_REPORT_UNIT gate: execution_path_actual must be declared "
            "(vc_submit / local_bash / local_docker).",
            file=sys.stderr,
        )
        return 1

    if execution_path_actual != "vc_submit":
        fallback_approved = bool(data.get("fallback_approved"))
        fallback_reason = data.get("local_fallback_reason", "")
        if not fallback_approved or not fallback_reason:
            print(
                "RUN_REPORT_UNIT gate: a non-vc_submit execution path requires "
                "fallback_approved=true and a non-empty local_fallback_reason. vc "
                "submit is the mandatory path when vc is available; document any "
                "deviation.",
                file=sys.stderr,
            )
            return 1

    print(f"run_report OK: execution_path_actual={execution_path_actual}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
