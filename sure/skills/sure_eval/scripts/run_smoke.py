#!/usr/bin/env python3
"""Gate script for the SMOKE_TEST_UNIT.

Runs a bounded smoke execution of the materialized run_evaluation.sh (or the
declared entrypoint) on a tiny sample slice and reports whether it passed.
Called by the Sure hook with:
    python3 scripts/run_smoke.py --run-dir <runDir> --produces <abs>

The smoke_test_result.json artifact (validated by the hook) is the source of
truth; this script re-confirms smoke_passed is true and the entrypoint exists.
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
    parser.add_argument("--produces", required=True, help="absolute path to smoke_test_result.json")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    path = Path(args.produces)
    if not path.exists():
        print(f"smoke_test_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"smoke_test_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if not data.get("smoke_passed"):
        failures = data.get("failures") or []
        detail = "\n  - " + "\n  - ".join(failures) if failures else ""
        print(f"smoke_test gate failed: smoke_passed is false.{detail}", file=sys.stderr)
        return 1

    # Cross-check: the materialized execution_surface entrypoint must exist.
    surface_path = run_dir / "artifacts" / "execution_surface.json"
    if surface_path.exists():
        try:
            surface = json.loads(surface_path.read_text(encoding="utf-8"))
            entrypoint = surface.get("entrypoint", "")
            # entrypoint may be a run-dir-relative path or absolute; resolve loosely.
            if entrypoint:
                cand = Path(entrypoint)
                if not cand.is_absolute():
                    cand = run_dir / cand
                if not cand.exists():
                    print(f"smoke_test gate: declared entrypoint does not exist: {cand}", file=sys.stderr)
                    return 1
        except json.JSONDecodeError:
            pass

    print("smoke_test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
