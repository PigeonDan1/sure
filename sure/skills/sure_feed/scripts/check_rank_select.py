#!/usr/bin/env python3
"""Gate script for the RANK_AND_SELECT unit.

Verifies the selection is non-empty, that every selected candidate was
actually synthesized upstream (cross-checked against the run directory's
model_input_result.json, not taken on the word of the artifact under test),
and that each one has a non-negative score + a repo path (required for
/sure_onboard handoff).
Called by the Sure hook:
    python3 scripts/check_rank_select.py --run-dir <runDir> --produces <abs>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def read_run_artifact(run_dir: Path, name: str) -> dict | None:
    """Read a state-machine artifact from the run directory, or None.

    Mirrors the hook's lookup order (hooks/checkpoints.ts artifactPath):
    artifacts/debug/ first, then the artifacts root.
    """
    for path in (run_dir / "artifacts" / "debug" / name, run_dir / "artifacts" / name):
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return value if isinstance(value, dict) else None
    return None


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

    # The selection is evidence about an earlier unit's output, so it is checked
    # against that output rather than against itself: a model that never got a
    # MODEL_INPUT synthesized cannot be ranked into the handoff.
    synthesized = read_run_artifact(Path(args.run_dir), "model_input_result.json")
    if synthesized is None:
        print(
            "RANK_AND_SELECT gate: model_input_result.json is missing or unreadable under "
            f"{Path(args.run_dir) / 'artifacts'}. The selection is verified against the "
            "MODEL_INPUT objects SYNTHESIZE_MODEL_INPUT produced, not against "
            "rank_select_result.json alone; rerun that unit before ranking.",
            file=sys.stderr,
        )
        return 1
    synthesized_ids = {
        entry.get("model_id")
        for entry in (synthesized.get("model_inputs") or [])
        if isinstance(entry, dict)
    }

    errors = []
    for cand in selected:
        if not isinstance(cand, dict):
            errors.append("a selected candidate is not an object")
            continue
        model_id = cand.get("model_id", "?")
        if model_id not in synthesized_ids:
            errors.append(
                f'candidate "{model_id}" has no MODEL_INPUT in model_input_result.json '
                f"(synthesized: {sorted(str(mid) for mid in synthesized_ids)}); rank only "
                "models that passed SYNTHESIZE_MODEL_INPUT"
            )
        score = cand.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or score < 0:
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
