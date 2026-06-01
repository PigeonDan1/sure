#!/usr/bin/env python3
"""
Check execution surface compliance.

Enforces the single rule: the execution surface MUST be generated from a
template under the templates/ directory. Nothing else matters.

This script MUST be called by the EXECUTION_READINESS_UNIT before any run
is approved for execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def check_template_source(
    run_dir: Path,
    expected_template: Path | None,
) -> dict[str, Any]:
    """Check that execution_surface.json declares a template under templates/."""
    surface_path = run_dir / "execution_surface.json"
    if not surface_path.exists():
        return {
            "passed": False,
            "template_declared": "",
            "template_exists": False,
            "under_templates_dir": False,
            "matches_expected": False,
            "evidence": "execution_surface.json not found",
        }

    try:
        data = json.loads(surface_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {
            "passed": False,
            "template_declared": "",
            "template_exists": False,
            "under_templates_dir": False,
            "matches_expected": False,
            "evidence": f"invalid JSON: {e}",
        }

    prov = data.get("source_provenance", {})
    template_file = prov.get("template_file", "")

    if not template_file:
        return {
            "passed": False,
            "template_declared": "",
            "template_exists": False,
            "under_templates_dir": False,
            "matches_expected": False,
            "evidence": "source_provenance.template_file is empty",
        }

    template_path = Path(template_file)
    under_templates = template_path.parts[0] == "templates" if template_path.parts else False
    exists = template_path.exists()
    matches_expected = False
    if expected_template is not None:
        matches_expected = template_path.resolve() == expected_template.resolve()

    passed = under_templates and exists
    if expected_template is not None:
        passed = passed and matches_expected

    evidence_parts = []
    if not under_templates:
        evidence_parts.append(f"template '{template_file}' is not under templates/")
    if not exists:
        evidence_parts.append(f"template '{template_file}' does not exist")
    if expected_template is not None and not matches_expected:
        evidence_parts.append(
            f"expected template '{expected_template}', got '{template_file}'"
        )

    return {
        "passed": passed,
        "template_declared": template_file,
        "template_exists": exists,
        "under_templates_dir": under_templates,
        "matches_expected": matches_expected,
        "evidence": "ok" if passed else "; ".join(evidence_parts),
    }


def check_source_provenance(run_dir: Path) -> dict[str, Any]:
    """Check execution_surface.json has source_provenance with template_file."""
    surface_path = run_dir / "execution_surface.json"
    if not surface_path.exists():
        return {
            "passed": False,
            "evidence": "execution_surface.json not found",
        }

    try:
        data = json.loads(surface_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {
            "passed": False,
            "evidence": f"invalid JSON: {e}",
        }

    prov = data.get("source_provenance")
    if not prov:
        return {
            "passed": False,
            "evidence": "source_provenance field missing",
        }

    template_file = prov.get("template_file", "")
    if not template_file:
        return {
            "passed": False,
            "evidence": "source_provenance.template_file is empty",
        }

    return {
        "passed": True,
        "template_file": template_file,
        "evidence": "source_provenance present",
    }


REQUIRED_EVALUATE_ARGS = [
    "--results-dir",
    "--protocol-id",
    "--model-dir",
]


def check_evaluate_predictions_args(run_dir: Path) -> dict[str, Any]:
    """Ensure run_evaluation.sh preserves required evaluate_predictions.py args."""
    shell_path = run_dir / "run_evaluation.sh"
    if not shell_path.exists():
        return {
            "passed": True,
            "evidence": "run_evaluation.sh not found, nothing to check",
        }

    content = shell_path.read_text(encoding="utf-8")

    # Only check if this script actually calls evaluate_predictions.py
    if "evaluate_predictions.py" not in content:
        return {
            "passed": True,
            "evidence": "no evaluate_predictions.py call found",
        }

    # Extract the evaluate_predictions.py call block (from the script line to the || line)
    lines = content.splitlines()
    eval_block_lines: list[str] = []
    in_eval_block = False
    for line in lines:
        if "evaluate_predictions.py" in line:
            in_eval_block = True
        if in_eval_block:
            eval_block_lines.append(line)
            if "|| EVAL_EXIT=$?" in line or "|| EVAL_EXIT" in line or line.strip().endswith("|| EVAL_EXIT=$?"):
                break
    eval_block = "\n".join(eval_block_lines)

    missing = [arg for arg in REQUIRED_EVALUATE_ARGS if arg not in eval_block]
    if missing:
        return {
            "passed": False,
            "evidence": f"missing required args in evaluate_predictions.py call: {missing}",
        }

    return {
        "passed": True,
        "evidence": "all required evaluate_predictions.py args present",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check execution surface compliance before run approval"
    )
    parser.add_argument("--run-dir", required=True, help="Path to the run directory")
    parser.add_argument(
        "--expected-template",
        help="Expected template path (e.g., templates/run_single_model_single_dataset.sh)",
    )
    parser.add_argument("--output", help="JSON output path")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    expected_template = Path(args.expected_template).resolve() if args.expected_template else None

    checks = {
        "template_source": check_template_source(run_dir, expected_template),
        "source_provenance": check_source_provenance(run_dir),
        "evaluate_predictions_args": check_evaluate_predictions_args(run_dir),
    }

    all_passed = all(c["passed"] for c in checks.values())
    blocking_issues: list[str] = []
    for name, result in checks.items():
        if not result["passed"]:
            blocking_issues.append(f"{name}: {result.get('evidence', 'failed')}")

    report = {
        "run_id": run_dir.name,
        "compliance_passed": all_passed,
        "checks": checks,
        "blocking_issues": blocking_issues,
    }

    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
