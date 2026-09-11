#!/usr/bin/env python3
"""Gate script for the run_report unit: validate main_agent_run_report.json.

Read-only. Called by the Sure hook with:
    python3 scripts/check_agent_run_report.py --run-dir <runDir> --produces <abs>

A completed run must prove the bundle and a successful evaluation; a failed
run must point at the failed upstream artifact and name a next_action.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SUCCESS_STATUSES = {"success", "completed", "complete", "passed"}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def gate_errors(run_dir: Path, report_path: Path) -> list[str]:
    report = _read_json(report_path)
    if report is None:
        return [f"main_agent_run_report.json not found or invalid: {report_path}"]
    artifacts = run_dir / "artifacts"
    spec = _read_json(artifacts / "agent_spec_resolved.json")
    if spec is None:
        return ["agent_spec_resolved.json not found or invalid; run scripts/resolve_agent.py first"]

    errors: list[str] = []
    if report.get("report_persisted") is not True:
        errors.append("report_persisted must be true — preview the report, get the user's confirmation, then persist it")
    if str(report.get("execution_path_actual") or "") != "local":
        errors.append("execution_path_actual must be 'local'")
    if str(report.get("agent_name") or "") != str(spec["agent"]["name"]):
        errors.append(f"agent_name {report.get('agent_name')!r} does not match the resolved spec {spec['agent']['name']!r}")
    run_dir_value = str(report.get("run_dir") or "")
    if run_dir_value != str(spec["runtime"]["product_dir"]):
        errors.append(f"run_dir must point at the agent product directory {spec['runtime']['product_dir']}")

    status = str(report.get("status") or "").lower()
    eval_report = _read_json(artifacts / "eval_run_report.json")
    if status in SUCCESS_STATUSES:
        if eval_report is None:
            errors.append("a completed run requires artifacts/eval_run_report.json")
        elif eval_report.get("status") != "success":
            errors.append("a completed run requires eval_run_report.json with status success")
    else:
        if not str(report.get("next_action") or "").strip():
            errors.append("a non-success run report must name next_action")
        if eval_report is None and _read_json(artifacts / "execution_result.json") is None:
            errors.append(
                "a non-success run report requires the failed eval_run_report.json or execution_result.json as evidence"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate main_agent_run_report.json for /sure_agent_eval")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to main_agent_run_report.json")
    args = parser.parse_args()
    errors = gate_errors(Path(args.run_dir), Path(args.produces))
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
