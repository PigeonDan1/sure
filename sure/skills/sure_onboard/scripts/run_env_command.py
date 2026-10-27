#!/usr/bin/env python3
"""Run one build_env shell command with per-attempt evidence (POSIX only)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

if os.name == "posix":
    import fcntl


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_state(directory: Path, state: dict) -> None:
    temporary = directory / "state.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(directory / "state.json")


def record_query(directory: Path, action: str, wait: float, state: dict) -> None:
    # Evidence only: the query cadence against log growth is what later
    # analysis reads; workers never parse this file.
    log = directory / "command.log"
    entry = dict(
        ts=timestamp(), action=action, wait=wait,
        status_seen=state.get("status"), processes_ended=bool(state.get("processes_ended")),
        log_bytes=log.stat().st_size if log.exists() else 0,
    )
    with (directory / "queries.jsonl").open("a", encoding="utf-8") as queries:
        queries.write(json.dumps(entry, ensure_ascii=False) + "\n")


def attempt_directory(run_dir: Path, attempt: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", attempt):
        raise ValueError("attempt must be the identity returned by start")
    return run_dir / "evidence" / "env-commands" / attempt


def status(run_dir: Path, attempt: str) -> dict:
    directory = attempt_directory(run_dir, attempt)
    with (directory / "owner.lock").open("rb") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            owned = False
        except BlockingIOError:
            owned = True
        state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        if state.get("attempt") != attempt or state.get("run_dir") != str(run_dir):
            raise ValueError("attempt identity does not match this run")
        if state["status"] not in ("succeeded", "failed"):
            state["status"] = "running" if owned else "unknown"
            state["processes_ended"] = False
        state["stop_requested"] = (directory / "stop").exists()
        return state


def wait_status(run_dir: Path, attempt: str, wait: float) -> dict:
    deadline = time.monotonic() + wait
    while True:
        state = status(run_dir, attempt)
        if state["status"] in ("succeeded", "failed") or time.monotonic() >= deadline:
            return state
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def group_has_children() -> bool:
    # This supervisor remains the session/group leader, so its group ID cannot
    # be recycled. No command-name matching or saved PID authorizes a signal.
    result = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True, check=True, timeout=5,
        start_new_session=True,
    )
    for line in result.stdout.splitlines():
        pid, group, state = line.split()
        if int(group) == os.getpid() and int(pid) != os.getpid() and not state.startswith("Z"):
            return True
    return False


def supervise(run_dir: Path, attempt: str) -> None:
    if os.getpgrp() != os.getpid():
        raise ValueError("internal monitor must own its process group")
    directory = attempt_directory(run_dir, attempt)
    with (directory / "owner.lock").open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        if state["status"] != "unknown" or state["started_at"] is not None:
            raise ValueError("an attempt cannot be restarted")
        stopping = False

        def request_stop(_signum: int, _frame: object) -> None:
            (directory / "stop").touch()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        state.update(status="running", started_at=timestamp(), supervisor_pid=os.getpid())
        write_state(directory, state)
        child = None
        ps_failures = 0
        try:
            group_has_children()
            # Each command inherits the caller's live environment, never a
            # persisted environment snapshot. Pipeline failures stay failures.
            child = subprocess.Popen(
                [state["shell"], "-o", "pipefail", "-c", state["command"]],
                cwd=state["cwd"], stdin=subprocess.DEVNULL,
            )
            while True:
                if (directory / "stop").exists() and not stopping:
                    stopping = True
                    os.killpg(os.getpid(), signal.SIGTERM)
                result = child.poll()
                if result is not None:
                    try:
                        ended = not group_has_children()
                        ps_failures = 0
                    except (OSError, subprocess.SubprocessError):
                        # One flaky inspection must not orphan a healthy
                        # attempt; only repeated loss of inspection does.
                        ps_failures += 1
                        ended = False
                        if ps_failures >= 3:
                            raise
                    if ended:
                        state.update(
                            status="succeeded" if result == 0 and not stopping else "failed",
                            returncode=result, ended_at=timestamp(), processes_ended=True,
                            stop_confirmed=stopping,
                        )
                        write_state(directory, state)
                        return
                time.sleep(0.1)
        except Exception as exc:
            # A lost supervisor or failed inspection is not permission to
            # clean the workspace, signal a saved PID, or launch a replacement.
            state.update(status="unknown", error=str(exc), processes_ended=False)
            if child is None:
                state.update(status="failed", ended_at=timestamp(), processes_ended=True)
            write_state(directory, state)
            raise


def start(run_dir: Path, cwd: Path, command: str, shell: str) -> str:
    if not cwd.is_dir() or not run_dir.is_dir():
        raise ValueError("run-dir and cwd must be existing directories")
    shell_path = shutil.which(shell)
    if shell_path is None or shutil.which("ps") is None:
        raise ValueError("a bash-compatible shell and POSIX ps are required")
    attempt = uuid.uuid4().hex
    directory = attempt_directory(run_dir, attempt)
    directory.mkdir(parents=True, mode=0o700)
    (directory / "owner.lock").touch()
    state = dict(
        run_dir=str(run_dir), attempt=attempt, cwd=str(cwd), command=command, shell=shell_path,
        log=str(directory / "command.log"), created_at=timestamp(), started_at=None, ended_at=None,
        status="unknown", returncode=None, processes_ended=False, stop_confirmed=False,
    )
    write_state(directory, state)
    with (directory / "command.log").open("ab", buffering=0) as log:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_supervise", "--run-dir", str(run_dir),
             "--attempt", attempt],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
    return attempt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop", "_supervise"))
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--attempt")
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("--command", help="inline build_env command; do not embed credentials")
    parser.add_argument("--shell", default="bash", help="existing bash-compatible shell")
    parser.add_argument("--wait", type=float, default=30, help="query wait seconds, never a process deadline")
    args = parser.parse_args()
    try:
        if os.name != "posix":
            raise ValueError("run_env_command requires POSIX process groups; use the existing foreground shell on this platform")
        if not math.isfinite(args.wait) or args.wait < 0:
            raise ValueError("wait must be a finite non-negative duration")
        run_dir = args.run_dir.expanduser().resolve()
        if args.action == "start":
            if args.cwd is None or not args.command:
                raise ValueError("start requires --cwd and --command")
            attempt = start(run_dir, args.cwd.expanduser().resolve(), args.command, args.shell)
        else:
            if args.attempt is None:
                raise ValueError("status/stop requires --attempt from start")
            attempt = args.attempt
            if args.action == "_supervise":
                supervise(run_dir, attempt)
                return 0
            current = status(run_dir, attempt)
            if args.action == "stop" and current["status"] == "running":
                (attempt_directory(run_dir, attempt) / "stop").touch()
        state = wait_status(run_dir, attempt, args.wait)
        record_query(attempt_directory(run_dir, attempt), args.action, args.wait, state)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return {"succeeded": 0, "running": 0, "failed": 1, "unknown": 2}[state["status"]]
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"run_env_command: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
