#!/usr/bin/env python3
"""Gate script for the SUBMIT_EXECUTION_UNIT.

Probes vc availability (`which vc && vc info`) and validates that the produced
submit_result.json matches the user-requested execution surface:
- execution=vc must submit through vc.
- execution=local may run locally even when vc is available.
- execution=auto prefers vc when available; local auto fallback must explain why.
The environment-derived vc_available field is stamped into the artifact.

Called by the Sure hook with:
    python3 scripts/vc_check.py --run-dir <runDir> --produces <abs>

exit 0 = pass; non-zero = fail (stderr carries the repair text).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def vc_is_available() -> bool:
    """Return True only when `which vc` AND `vc info` both succeed.

    Per the execution policy gate, `vc --version` MUST NOT be used to
    determine availability.
    """
    if not shutil.which("vc"):
        return False
    try:
        result = subprocess.run(
            ["vc", "info"], capture_output=True, text=True, timeout=30
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _execution_requested(data: dict, run_dir: Path) -> str:
    execution = data.get("execution")
    if isinstance(execution, dict) and isinstance(execution.get("requested"), str):
        return execution["requested"]
    if isinstance(data.get("execution_requested"), str):
        return data["execution_requested"]

    eval_input = _read_json(run_dir / "artifacts" / "eval_input_resolved.json")
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    resolved_execution = runtime.get("execution") if isinstance(runtime.get("execution"), dict) else {}
    if isinstance(resolved_execution.get("requested"), str):
        return resolved_execution["requested"]

    execution_path = str(data.get("execution_path") or "")
    if execution_path == "vc_submit":
        return "vc"
    if execution_path in {"local_bash", "local_docker"}:
        return "local"
    return "auto"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to submit_result.json")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    path = Path(args.produces)
    if not path.exists():
        print(f"submit_result.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"submit_result.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    available = vc_is_available()
    # Stamp the environment-derived availability into the artifact so the
    # in-process gate agrees with reality. Never trust a model-supplied
    # vc_available value; a forged false must not bypass local execution checks.
    if data.get("vc_available") != available:
        data["vc_available"] = available
        try:
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    execution_path = data.get("execution_path", "")
    requested = _execution_requested(data, run_dir)
    local_paths = {"local_bash", "local_docker"}

    if requested == "vc" and execution_path != "vc_submit":
        print(
            "SUBMIT_EXECUTION gate: user requested execution=vc, so "
            "submit_result.json.execution_path must be vc_submit.",
            file=sys.stderr,
        )
        return 1
    if requested == "vc" and not available:
        print(
            "SUBMIT_EXECUTION gate: user requested execution=vc, but `which vc && vc info` did not pass.",
            file=sys.stderr,
        )
        return 1
    if requested == "local" and execution_path not in local_paths:
        print(
            "SUBMIT_EXECUTION gate: user requested execution=local, so execution_path must be local_bash or local_docker.",
            file=sys.stderr,
        )
        return 1
    if requested == "auto" and available and execution_path != "vc_submit":
        fallback_approved = bool(data.get("fallback_approved"))
        fallback_reason = data.get("local_fallback_reason", "")
        if not fallback_approved or not fallback_reason:
            print(
                "SUBMIT_EXECUTION gate: execution=auto and vc is available "
                "(`which vc && vc info` passed), so execution_path must be vc_submit. "
                "If this is an intentional local fallback, set fallback_approved=true "
                "and a non-empty local_fallback_reason.",
                file=sys.stderr,
            )
            return 1
    if requested == "auto" and not available and execution_path in local_paths and not data.get("local_fallback_reason"):
        data["local_fallback_reason"] = "execution=auto selected local execution because vc is unavailable"
        try:
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    print(f"vc_check OK: requested={requested}, vc_available={available}, execution_path={execution_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
