#!/usr/bin/env python3
"""Gate script for the FETCH_WEIGHTS unit.

Verifies weights_ready is true and (when weights.required) the resolved local
model path exists. Enforces the model-local-first checkpoint rule: a fallback
to host-global paths must carry a reason.
Called by the Sure hook:
    python3 scripts/check_weights.py --run-dir <runDir> --produces <abs>
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
        print(f"weights_manifest.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"weights_manifest.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if not data.get("weights_ready"):
        print("FETCH_WEIGHTS gate failed: weights_ready is false.", file=sys.stderr)
        return 1

    resolved = data.get("resolved_local_model_path")
    if resolved and not Path(resolved).exists():
        print(
            f"FETCH_WEIGHTS gate: resolved_local_model_path does not exist: {resolved}",
            file=sys.stderr,
        )
        return 1

    if data.get("fallback_to_host_global"):
        reason = data.get("fallback_reason", "")
        if not reason:
            print(
                "FETCH_WEIGHTS gate: fallback_to_host_global is true but no "
                "fallback_reason recorded. Document why weights could not be "
                "resolved model-local.",
                file=sys.stderr,
            )
            return 1

    print(f"check_weights OK: weights_ready=true, source={data.get('source')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
