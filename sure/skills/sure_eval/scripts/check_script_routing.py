#!/usr/bin/env python3
"""Gate script for the SCRIPT_ROUTING_UNIT.

Verifies that script_routing.json declares only whitelisted step names whose
scripts resolve under scripts/ and exist on disk. The step NAMES are the short
contract names (prepare_dataset / materialize_templates / wait_for_predictions
/ validate_predictions / evaluate_predictions) — the
authoritative names used by the contract
(references/contracts/main_agent_script_routing_unit.md) and the produces
exemplar (scripts/templates/main_agent_script_routing.json). Each step's
`script` is the real file under scripts/ that the name maps to.
submit_vc_run is NOT a script_routing step: it is a separate gate unit (produces
submit_result.json, validated by vc_check.py) that invokes `vc submit` outside
the run_evaluation.sh pipeline.

Called by the Sure hook with:
    python3 scripts/check_script_routing.py --run-dir <runDir> --produces <abs>

exit 0 = pass; non-zero = fail (stderr carries the repair text).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Short contract step names (the authoritative whitelist). Each maps to a real
# script under scripts/ — see STEP_TO_SCRIPT. Names match the produces exemplar
# (scripts/templates/main_agent_script_routing.json) and the contract doc.
STEP_WHITELIST = [
    "prepare_dataset",
    "materialize_templates",
    "wait_for_predictions",
    "validate_predictions",
    "evaluate_predictions",
]

# Map each contract step name to the deterministic script file it routes to.
# Used to (a) verify the declared script matches the expected file, and (b)
# produce a helpful repair message naming the expected script.
STEP_TO_SCRIPT = {
    "prepare_dataset": "scripts/prepare_sure_dataset.py",
    "materialize_templates": "scripts/materialize_predictions_template.py",
    "wait_for_predictions": "scripts/generate_predictions_via_server.py",
    "validate_predictions": "scripts/validate_prediction_files.py",
    "evaluate_predictions": "scripts/evaluate_predictions.py",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to script_routing.json")
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        print(f"script_routing.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"script_routing.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        print("script_routing.json must declare a non-empty steps array", file=sys.stderr)
        return 1

    # Resolve script paths relative to the skill package dir (parent of scripts/).
    pkg_dir = Path(__file__).resolve().parent.parent
    errors: list[str] = []
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"step[{idx}] is not an object")
            continue
        name = step.get("name")
        if name not in STEP_WHITELIST:
            errors.append(
                f'step[{idx}].name "{name}" is not in the whitelist '
                f"{STEP_WHITELIST}. Use the short contract name; it routes to "
                f"a script under scripts/."
            )
            continue
        expected_script = STEP_TO_SCRIPT.get(name, "")
        script = step.get("script", "")
        if not script:
            errors.append(
                f'step[{idx}] ("{name}") is missing the script field; expected '
                f'"{expected_script}".'
            )
        elif not script.startswith("scripts/"):
            errors.append(
                f'step[{idx}] ("{name}") script must resolve under scripts/ '
                f'(got "{script}"); expected "{expected_script}".'
            )
        elif script != expected_script:
            errors.append(
                f'step[{idx}] ("{name}") script "{script}" does not match the '
                f'expected "{expected_script}" for this step name.'
            )
        else:
            # File-must-exist constraint from the contract.
            script_abs = pkg_dir / script
            if not script_abs.exists():
                errors.append(
                    f'step[{idx}] ("{name}") script "{script}" does not exist on '
                    f"disk at {script_abs}."
                )
    if errors:
        print("script_routing gate failed:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 1
    print(f"script_routing OK: {len(steps)} step(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
