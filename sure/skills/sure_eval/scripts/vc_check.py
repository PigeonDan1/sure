#!/usr/bin/env python3
"""Gate script for the SUBMIT_VC_RUN_UNIT — red line 2 (vc mandatory).

Probes vc availability (`which vc && vc info`) and verifies that when vc is
available, submit_result.json.execution_path is vc_submit. Any local fallback
must carry fallback_approved + local_fallback_reason. Writes the vc_available
field back into the artifact if the agent omitted it.

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

    Per the VC_SUBMIT_MANDATORY red line, `vc --version` MUST NOT be used to
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to submit_result.json")
    args = parser.parse_args()

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
    if available and execution_path != "vc_submit":
        fallback_approved = bool(data.get("fallback_approved"))
        fallback_reason = data.get("local_fallback_reason", "")
        if not fallback_approved or not fallback_reason:
            print(
                "VC_SUBMIT_MANDATORY red line: vc is available (which vc && vc info "
                "passed) so execution_path must be vc_submit. Local execution is "
                "strictly prohibited. If you must fall back, set fallback_approved=true "
                "and a non-empty local_fallback_reason — but the run will be marked INVALID.",
                file=sys.stderr,
            )
            return 1

    print(f"vc_check OK: vc_available={available}, execution_path={execution_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
