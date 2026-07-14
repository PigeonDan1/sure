#!/usr/bin/env python3
"""Gate script for the VERDICT unit.

Verifies the verdict status is a terminal value, and a success/passed verdict
requires build.success + all four validation tests passed. Includes the Docker
optional branch: when backend.type=docker, build.success may be achieved via a
docker_image instead of a local venv, but the validation requirements are
identical.

Called by the Sure hook:
    python3 scripts/check_verdict.py --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FOUR_TESTS = ["import_test", "load_test", "infer_test", "contract_test"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        print(f"verdict.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"verdict.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    status = data.get("status", "")
    if status not in ("passed", "success", "failed", "partial"):
        print(
            f'VERDICT gate: status must be a terminal value ("passed"/"success", '
            f'"failed", or "partial"); got "{status}".',
            file=sys.stderr,
        )
        return 1

    if status in ("passed", "success"):
        build = data.get("build") or {}
        if not build.get("success"):
            print(
                "VERDICT gate: status is success but build.success is false.",
                file=sys.stderr,
            )
            return 1
        validation = data.get("validation") or {}
        failed_tests = [
            t for t in FOUR_TESTS if not (validation.get(t) or {}).get("passed")
        ]
        if failed_tests:
            print(
                "VERDICT gate: status is success but these validation tests are "
                "not passed=true: " + ", ".join(failed_tests),
                file=sys.stderr,
            )
            return 1

        # Cross-check declared artifact paths exist (spec + wrapper at minimum).
        artifacts = data.get("artifacts") or {}
        spec_path = artifacts.get("spec_path")
        if spec_path and not Path(spec_path).exists():
            print(f"VERDICT gate: declared spec_path does not exist: {spec_path}", file=sys.stderr)
            return 1
        wrapper_path = artifacts.get("wrapper_path")
        if wrapper_path and not Path(wrapper_path).exists():
            print(f"VERDICT gate: declared wrapper_path does not exist: {wrapper_path}", file=sys.stderr)
            return 1

    print(f"check_verdict OK: status={status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
