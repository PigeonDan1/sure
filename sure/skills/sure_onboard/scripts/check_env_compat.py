#!/usr/bin/env python3
"""Gate script for the VALIDATE_ENV_COMPAT unit.

Verifies the built env can actually load the resolved weights on the available
device, the python version matches, and the adapter protocol is supported.
Called by the Sure hook:
    python3 scripts/check_env_compat.py --run-dir <runDir> --produces <abs>
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
        print(f"env_compat_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"env_compat_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if not data.get("compat_ok"):
        incompat = data.get("incompatibilities") or []
        detail = "\n  - " + "\n  - ".join(incompat) if incompat else ""
        print(
            "VALIDATE_ENV_COMPAT gate failed: compat_ok is false. Confirm the "
            "built env loads the resolved weights on the available device, the "
            f"python version matches, and the adapter protocol is supported.{detail}",
            file=sys.stderr,
        )
        return 1

    print(
        f"check_env_compat OK: compat_ok=true, device={data.get('device')}, "
        f"python_match={data.get('python_version_match')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
