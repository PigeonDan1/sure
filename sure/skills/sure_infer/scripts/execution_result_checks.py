"""Read-only consistency checks for execution_result.json.

Lifted out of the former wait_vc_execution.py so the gate that validates a
local execution result no longer depends on the VC submission path. Nothing
here touches the filesystem: callers load the JSON and pass the dict in.
"""
from __future__ import annotations

from typing import Any

TERMINAL_JOB_STATUSES = {"succeeded", "failed", "partial"}


def validation_errors(execution: dict[str, Any], expected_path: str) -> list[str]:
    """Return the reasons ``execution`` is not an acceptable terminal result.

    ``expected_path`` is the execution path the run planned (for example the
    ``execution.path_planned`` of execution_surface.json). Pass ``""`` to skip
    the path comparison.
    """
    errors: list[str] = []
    status = str(execution.get("job_status") or "")
    if status == "running":
        errors.append(
            "execution is still running; this is not a failure. Wait for the "
            "process to finish and re-run the gate instead of reading logs to decide."
        )
    elif status not in TERMINAL_JOB_STATUSES:
        errors.append(f"execution_result.json has invalid job_status: {status!r}")
    actual_path = str(execution.get("execution_path") or "")
    if expected_path and actual_path != expected_path:
        errors.append(
            f"execution_result.json execution_path {actual_path!r} differs from the planned {expected_path!r}"
        )
    if status == "succeeded" and execution.get("exit_code") != 0:
        errors.append("succeeded execution_result.json must declare exit_code=0")
    if status == "failed" and execution.get("exit_code") in (None, 0):
        errors.append("failed execution_result.json must declare a non-zero exit_code")
    return errors
