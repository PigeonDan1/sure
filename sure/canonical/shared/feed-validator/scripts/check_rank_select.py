#!/usr/bin/env python3
"""Gate script for the RANK_AND_SELECT unit.

Verifies the selection is non-empty and every selected candidate has a
non-negative score + a repo path (required for /sure_onboard handoff).
Called by the Sure hook:
    python3 scripts/check_rank_select.py --run-dir <runDir> --produces <abs>
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
        print(f"rank_select_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"rank_select_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    selected = data.get("selected") or []
    if not selected:
        print(
            "RANK_AND_SELECT gate: selected array is empty. Rank candidates and "
            "select at least one for handoff (or finish with status incomplete "
            "if none qualify).",
            file=sys.stderr,
        )
        return 1

    errors = []
    for cand in selected:
        if not isinstance(cand, dict):
            errors.append("a selected candidate is not an object")
            continue
        model_id = cand.get("model_id", "?")
        score = cand.get("score")
        if not isinstance(score, (int, float)) or score < 0:
            got = "missing" if score is None else repr(score)
            errors.append(
                f'candidate "{model_id}" score must be a non-negative number '
                f"(score >= 0, e.g. a similarity or quality score); got {got}"
            )
        if not cand.get("repo"):
            errors.append(
                f'candidate "{model_id}" has no repo path (required for handoff)'
            )
    if errors:
        print("RANK_AND_SELECT gate failed:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 1
    print(f"check_rank_select OK: {len(selected)} selected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
