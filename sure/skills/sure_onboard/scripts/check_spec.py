#!/usr/bin/env python3
"""Gate script for the VALIDATE_SPEC unit.

Verifies the seven spec checks all pass and status is terminal-passed. Reads
spec_validation.json. Called by the Sure hook:
    python3 scripts/check_spec.py --run-dir <runDir> --produces <abs>

exit 0 = pass; non-zero = fail (stderr carries the repair text).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SEVEN_CHECKS = [
    "spec_completeness",
    "evidence_sufficiency",
    "conflict_resolution",
    "build_plan_executable",
    "fixture_availability",
    "io_contract_sufficient",
    "preflight_compatible",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        print(f"spec_validation.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"spec_validation.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    checks = data.get("checks") or {}
    failed = [c for c in SEVEN_CHECKS if not (checks.get(c) or {}).get("passed")]
    status = data.get("status", "")
    if status == "failed" or failed:
        blockers = data.get("blockers") or []
        detail = ""
        if failed:
            detail = "\n  - failed checks: " + ", ".join(failed)
        if blockers:
            detail += "\n  - blockers: " + "; ".join(blockers)
        print(f"VALIDATE_SPEC gate failed (status={status}).{detail}", file=sys.stderr)
        return 1
    print(f"check_spec OK: all 7 checks passed, status={status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
