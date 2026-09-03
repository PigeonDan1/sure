#!/usr/bin/env python3
"""Gate script for the ASSESSMENT_UNIT.

Verifies that an anomalous result is user-confirmed. Called by the Sure hook:
    python3 scripts/check_assessment.py --run-dir <runDir> --produces <abs>

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
    parser.add_argument("--produces", required=True, help="absolute path to assessment_report.json")
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        print(f"assessment_report.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"assessment_report.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    anomaly = bool(data.get("anomaly_detected"))
    confirmed = bool(data.get("user_confirmed"))
    if anomaly and not confirmed:
        print(
            "ASSESSMENT_UNIT gate: anomaly_detected is true but user_confirmed is "
            "false. Surface the anomaly to the user and obtain confirmation before "
            "proceeding to RUN_REPORT_UNIT.",
            file=sys.stderr,
        )
        return 1

    status = data.get("status", "")
    if status in ("blocked", "needs_human_input") and not confirmed:
        print(
            f'ASSESSMENT_UNIT status is "{status}". Resolve the blocking issue or '
            "obtain user confirmation before advancing.",
            file=sys.stderr,
        )
        return 1

    print(f"assessment OK: anomaly={anomaly}, confirmed={confirmed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
