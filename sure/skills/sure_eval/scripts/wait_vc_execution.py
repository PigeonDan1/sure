#!/usr/bin/env python3
"""Wait for a submitted VC run and materialize its authoritative result."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


TERMINAL_JOB_STATUSES = {"succeeded", "failed", "partial"}
VC_TERMINAL_STATUSES = {"completed", "succeeded", "failed", "error", "terminated", "aborted"}
VC_MISSING_MARKERS = ("job\u4e0d\u5b58\u5728", "\u672a\u67e5\u5230\u8be5\u4efb\u52a1\u4fe1\u606f", "not found", "does not exist")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _path_under_run(run_dir: Path, value: str, *, default: Path | None = None) -> Path:
    path = Path(value).expanduser() if value else default
    if path is None:
        raise ValueError("required run-local path is missing")
    if not path.is_absolute():
        path = run_dir / path
    path = path.resolve()
    try:
        path.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"control artifact path must stay under the run directory: {path}") from exc
    return path


def _run_vc(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def _excerpt(completed: subprocess.CompletedProcess[str]) -> str:
    return (completed.stdout or completed.stderr or "").strip()[-2000:]


def _vc_terminal_evidence(info: str, describe: str) -> bool:
    end_match = re.search(r"(?mi)^EndTime:\s*(.+?)\s*$", info)
    if end_match and end_match.group(1).strip():
        return True
    status_match = re.search(r"(?mi)^Status:\s*([A-Za-z_-]+)\s*$", info)
    if status_match and status_match.group(1).lower() in VC_TERMINAL_STATUSES:
        return True
    return False


def _vc_missing_evidence(info: str, describe: str) -> bool:
    info_lower = info.lower()
    describe_lower = describe.lower()
    return any(marker in info_lower for marker in VC_MISSING_MARKERS) and any(
        marker in describe_lower for marker in VC_MISSING_MARKERS
    )


def _vc_job_observed(*results: subprocess.CompletedProcess[str]) -> bool:
    for result in results:
        excerpt = _excerpt(result)
        if result.returncode != 0 or not excerpt:
            continue
        if not any(marker in excerpt.lower() for marker in VC_MISSING_MARKERS):
            return True
    return False


def _duration_seconds(started_at: Any, ended_at: Any) -> float | None:
    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        ended = datetime.fromisoformat(str(ended_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (ended - started).total_seconds())


def _execution_payload(
    *,
    submit: dict[str, Any],
    job_status: str,
    exit_code: int | None,
    completion_source: str,
    terminal_status_path: str,
    poll_count: int,
    timed_out: bool,
    sentinel: dict[str, Any] | None,
    info: subprocess.CompletedProcess[str] | None,
    describe: subprocess.CompletedProcess[str] | None,
) -> dict[str, Any]:
    started_at = (sentinel or {}).get("started_at") or submit.get("submitted_at") or ""
    ended_at = (sentinel or {}).get("ended_at") or _utc_now()
    duration = (sentinel or {}).get("duration_seconds")
    if not isinstance(duration, (int, float)):
        duration = _duration_seconds(started_at, ended_at)
    payload: dict[str, Any] = {
        "job_status": job_status,
        "vc_job_id": str(submit.get("vc_job_id") or ""),
        "execution_path": "vc_submit",
        "execution_requested": str(submit.get("execution_requested") or "vc"),
        "log_path": str(submit.get("stdout_log") or submit.get("stderr_log") or ""),
        "host": str(submit.get("host") or submit.get("vc_job_id") or ""),
        "command": str(submit.get("command") or ""),
        "cwd": str(submit.get("cwd") or ""),
        "started_at": str(started_at),
        "ended_at": str(ended_at),
        "stdout_log": str(submit.get("stdout_log") or ""),
        "stderr_log": str(submit.get("stderr_log") or ""),
        "device_request": str(submit.get("device_request") or ""),
        "device_actual": str(submit.get("device_actual") or ""),
        "cuda_visible_devices": str(submit.get("cuda_visible_devices") or ""),
        "completion_source": completion_source,
        "submission_token": str(submit.get("submission_token") or ""),
        "terminal_sentinel": terminal_status_path,
        "timed_out": timed_out,
        "poll_count": poll_count,
        "vc_info_excerpt": _excerpt(info) if info is not None else "",
        "vc_describe_excerpt": _excerpt(describe) if describe is not None else "",
    }
    if completion_source == "wait_timeout":
        # Spelled out because the alternative reading — "the run failed" — sends
        # the caller off reading logs, and every one of those diagnostic detours
        # re-enters the gate and burns a retry on a job that is simply still busy.
        payload["next_action"] = (
            "wait_timeout: the waiter stopped, not the job. Run "
            "scripts/wait_vc_execution.py --run-dir <run_dir> --wait again; raise "
            "--timeout-seconds if this job is expected to take longer."
        )
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if duration is not None:
        payload["duration_seconds"] = duration
    return payload


def _validated_sentinel(path: Path, submit: dict[str, Any]) -> dict[str, Any] | None:
    sentinel = _read_json(path)
    if not sentinel:
        return None
    if sentinel.get("schema") != "sure.eval.vc_terminal_status.v1":
        raise ValueError(f"invalid VC terminal sentinel schema: {path}")
    if sentinel.get("submission_token") != submit.get("submission_token"):
        raise ValueError("VC terminal sentinel does not match the current submission token")
    exit_code = sentinel.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise ValueError("VC terminal sentinel exit_code must be an integer")
    expected_status = "succeeded" if exit_code == 0 else "failed"
    if sentinel.get("job_status") != expected_status:
        raise ValueError("VC terminal sentinel job_status conflicts with exit_code")
    return sentinel


def wait_for_vc_execution(
    *,
    run_dir: Path,
    submit: dict[str, Any],
    timeout_seconds: float,
    poll_interval_seconds: float,
    terminal_grace_seconds: float,
    run_vc: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run_vc,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if submit.get("execution_path") != "vc_submit":
        raise ValueError("wait_vc_execution.py only waits for execution_path=vc_submit")
    job_id = str(submit.get("vc_job_id") or "")
    token = str(submit.get("submission_token") or "")
    if not job_id or not token:
        raise ValueError("VC submit_result.json must declare vc_job_id and submission_token")
    terminal_relative = str(submit.get("terminal_status_path") or "")
    terminal_path = _path_under_run(run_dir, terminal_relative)
    deadline = monotonic() + max(0.0, timeout_seconds)
    terminal_seen_at: float | None = None
    job_observed = False
    poll_count = 0
    last_info: subprocess.CompletedProcess[str] | None = None
    last_describe: subprocess.CompletedProcess[str] | None = None

    while True:
        sentinel = _validated_sentinel(terminal_path, submit)
        if sentinel is not None:
            return _execution_payload(
                submit=submit,
                job_status=str(sentinel["job_status"]),
                exit_code=int(sentinel["exit_code"]),
                completion_source="terminal_sentinel",
                terminal_status_path=terminal_relative,
                poll_count=poll_count,
                timed_out=False,
                sentinel=sentinel,
                info=last_info,
                describe=last_describe,
            )

        last_info = run_vc(["vc", "info", "--job", job_id])
        last_describe = run_vc(["vc", "describe", "--job", job_id])
        poll_count += 1
        now = monotonic()
        info_excerpt = _excerpt(last_info)
        describe_excerpt = _excerpt(last_describe)
        job_observed = job_observed or _vc_job_observed(last_info, last_describe)
        terminal_evidence = _vc_terminal_evidence(info_excerpt, describe_excerpt) or (
            job_observed and _vc_missing_evidence(info_excerpt, describe_excerpt)
        )
        if terminal_evidence:
            terminal_seen_at = terminal_seen_at if terminal_seen_at is not None else now
            if now - terminal_seen_at >= max(0.0, terminal_grace_seconds):
                return _execution_payload(
                    submit=submit,
                    job_status="failed",
                    exit_code=1,
                    completion_source="vc_terminal_without_sentinel",
                    terminal_status_path=terminal_relative,
                    poll_count=poll_count,
                    timed_out=False,
                    sentinel=None,
                    info=last_info,
                    describe=last_describe,
                )
        else:
            terminal_seen_at = None

        if now >= deadline:
            return _execution_payload(
                submit=submit,
                job_status="running",
                exit_code=None,
                completion_source="wait_timeout",
                terminal_status_path=terminal_relative,
                poll_count=poll_count,
                timed_out=True,
                sentinel=None,
                info=last_info,
                describe=last_describe,
            )
        sleep(min(max(0.1, poll_interval_seconds), max(0.1, deadline - now)))


def _validation_errors(result: dict[str, Any], submit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    status = str(result.get("job_status") or "")
    if status == "running":
        errors.append(
            "VC execution is still running; this is not a failure. Rerun "
            "wait_vc_execution.py --wait (optionally with a larger --timeout-seconds). "
            "Do not read job logs to decide: every such detour re-enters this gate."
        )
    elif status not in TERMINAL_JOB_STATUSES:
        errors.append(f"execution_result.json has invalid job_status: {status!r}")
    if result.get("execution_path") != submit.get("execution_path"):
        errors.append("execution_result.json execution_path differs from submit_result.json")
    if submit.get("execution_path") == "vc_submit":
        if result.get("vc_job_id") != submit.get("vc_job_id"):
            errors.append("execution_result.json vc_job_id differs from submit_result.json")
        token = submit.get("submission_token")
        if token and result.get("submission_token") != token:
            errors.append("execution_result.json submission_token differs from submit_result.json")
        if token and not result.get("completion_source"):
            errors.append("execution_result.json completion_source is required for tokenized VC submissions")
    if status == "succeeded" and result.get("exit_code") != 0:
        errors.append("succeeded execution_result.json must declare exit_code=0")
    if status == "failed" and result.get("exit_code") in (None, 0):
        errors.append("failed execution_result.json must declare a non-zero exit_code")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", help="Path to execution_result.json")
    parser.add_argument("--wait", action="store_true", help="Wait and write execution_result.json")
    parser.add_argument("--timeout-seconds", type=float, default=7200)
    parser.add_argument("--poll-interval-seconds", type=float, default=10)
    parser.add_argument("--terminal-grace-seconds", type=float, default=30)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    artifacts_dir = run_dir / "artifacts"
    output_path = _path_under_run(
        run_dir,
        args.produces or "",
        default=artifacts_dir / "execution_result.json",
    )
    submit = _read_json(artifacts_dir / "submit_result.json")
    if not submit:
        print(f"submit_result.json not found or invalid under {artifacts_dir}", file=sys.stderr)
        return 1

    if args.wait:
        if submit.get("execution_path") != "vc_submit":
            result = _read_json(output_path)
            errors = _validation_errors(result, submit)
            if errors:
                print("execute_wait gate failed: " + "; ".join(errors), file=sys.stderr)
                return 1
        else:
            try:
                result = wait_for_vc_execution(
                    run_dir=run_dir,
                    submit=submit,
                    timeout_seconds=args.timeout_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                    terminal_grace_seconds=args.terminal_grace_seconds,
                )
            except ValueError as exc:
                print(f"execute_wait failed: {exc}", file=sys.stderr)
                return 1
            _write_json_atomic(output_path, result)
    else:
        result = _read_json(output_path)

    errors = _validation_errors(result, submit)
    if errors:
        print("execute_wait gate failed: " + "; ".join(errors), file=sys.stderr)
        return 1
    print(
        f"execute_wait OK: job_status={result.get('job_status')} "
        f"completion_source={result.get('completion_source', 'local_process')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
