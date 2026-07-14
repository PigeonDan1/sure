#!/usr/bin/env python3
"""Gate script for the BUILD_ENV unit.

Verifies env_ready is true and (when declared) the lockfile/docker image exists.
Called by the Sure hook:
    python3 scripts/check_env.py --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        print(f"build_env_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"build_env_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if not data.get("env_ready"):
        failures = data.get("failures") or []
        detail = "\n  - " + "\n  - ".join(failures) if failures else ""
        print(f"BUILD_ENV gate failed: env_ready is false.{detail}", file=sys.stderr)
        return 1

    # If a lockfile path is declared, it should exist (docker backend may
    # declare a docker_image instead — both are optional per backend).
    backend = data.get("backend", "")
    lockfile = data.get("lockfile_path")
    if lockfile and backend != "docker":
        if not Path(lockfile).exists():
            print(f"BUILD_ENV gate: declared lockfile does not exist: {lockfile}", file=sys.stderr)
            return 1

    print(f"check_env OK: env_ready=true, backend={backend}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
