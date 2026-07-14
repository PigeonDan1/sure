#!/usr/bin/env python3
"""Gate script for the MATCH_TASK unit.

Verifies every candidate carries a task match with match_source provenance
(strong "tasks" match or weak "custom_tag" fallback). Blocks false-positives.
Called by the Sure hook:
    python3 scripts/check_match_task.py --run-dir <runDir> --produces <abs>
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
        print(f"match_task_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"match_task_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    candidates = data.get("candidates") or []
    if not candidates:
        print("MATCH_TASK gate: candidates array is empty.", file=sys.stderr)
        return 1

    errors = []
    for cand in candidates:
        if not isinstance(cand, dict):
            errors.append("a candidate is not an object")
            continue
        model_id = cand.get("model_id", "?")
        match = cand.get("match")
        if not isinstance(match, dict):
            errors.append(f'candidate "{model_id}" has no match object')
            continue
        if match.get("matched") and not match.get("match_source"):
            errors.append(
                f'candidate "{model_id}" matched but match_source is empty '
                '(record "tasks" or "custom_tag")'
            )
    if errors:
        print("MATCH_TASK gate failed:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 1
    print(f"check_match_task OK: {len(candidates)} candidate(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
