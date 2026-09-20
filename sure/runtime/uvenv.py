#!/usr/bin/env python3
"""Shared uv virtual-environment helpers for SURE's Python runtimes.

The Model, Harness and Evaluation runtimes are the same shape: create an empty
uv virtual environment, sync a hash-locked requirement set into it, record what
landed, and publish the staging directory under its content-addressed name.
Only the flags and the surrounding bookkeeping differ, so this module holds the
parts that have to stay identical and leaves each runtime its own error type,
logging and manifest. The command builders return argument lists rather than
running anything, because the three callers log and time their commands
differently and a runner with eight switches would be worse than three calls.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping, Sequence


class UvEnvError(RuntimeError):
    """A uv environment could not be prepared or identified."""


def runtime_python_relative() -> str:
    return "Scripts/python.exe" if os.name == "nt" else "bin/python"


def child_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Launch a runtime interpreter without another interpreter's overrides."""
    env = dict(os.environ if source is None else source)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
        env.pop(key, None)
    return env


@contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            # LK_LOCK retries ten times at one-second intervals and then raises
            # EDEADLOCK. A cold start fetches an interpreter and every wheel, so
            # the loser of the race needs to wait far longer than that; retry
            # without a cap, matching the unbounded flock() below. Waiting
            # forever is safe: Windows releases a byte-range lock when the
            # holding process dies. A contended byte reports EACCES.
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EDEADLOCK):
                        raise
                    time.sleep(0.25)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def uv_binary(
    explicit: str | None = None,
    *,
    error: type[Exception] = UvEnvError,
    message: str = "uv is required to materialize a locked Python runtime",
) -> str:
    candidate = explicit or os.environ.get("SURE_UV_BIN", "").strip() or shutil.which("uv") or ""
    if not candidate or not Path(candidate).is_file():
        raise error(message)
    return str(Path(candidate).resolve())


def probe(python: Path, *, error: type[Exception] = UvEnvError) -> dict[str, str]:
    """Identify one interpreter: version, ABI, platform and its base binary."""
    code = (
        "import hashlib,json,platform,sys,sysconfig;"
        "base=__import__('pathlib').Path(sys._base_executable).resolve();"
        "print(json.dumps({'python_version':platform.python_version(),"
        "'python_abi':sysconfig.get_config_var('SOABI') or sys.implementation.cache_tag or '',"
        "'python_platform':sysconfig.get_platform(),"
        "'base_python':str(base),"
        "'base_python_sha256':hashlib.sha256(base.read_bytes()).hexdigest()}))"
    )
    completed = subprocess.run(
        [str(python), "-I", "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=child_environment(),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise error(f"Python identity probe failed: {detail}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise error("Python identity probe returned invalid JSON") from exc
    if not isinstance(value, dict) or not all(isinstance(item, str) and item for item in value.values()):
        raise error("Python identity probe returned incomplete identity data")
    return value


def venv_command(uv: str, staging: Path, *, python: str, allow_python_downloads: bool) -> list[str]:
    """`uv venv`. Pass a version like "3.11" to let uv fetch an interpreter."""
    command = [uv, "venv", "--no-project"]
    if not allow_python_downloads:
        command.append("--no-python-downloads")
    command.extend(["--python", python, str(staging)])
    return command


def sync_command(
    uv: str,
    runtime_python: Path,
    lock_path: Path,
    *,
    allow_python_downloads: bool,
    allow_empty: bool = False,
    extra: Sequence[str] = (),
) -> list[str]:
    """`uv pip sync`, always hash-checked and always strict."""
    command = [uv, "pip", "sync", "--python", str(runtime_python), "--require-hashes", "--strict"]
    if allow_empty:
        command.append("--allow-empty-requirements")
    if not allow_python_downloads:
        command.append("--no-python-downloads")
    command.extend(extra)
    command.append(str(lock_path))
    return command


def freeze_command(uv: str, runtime_python: Path, *, allow_python_downloads: bool) -> list[str]:
    command = [uv, "pip", "freeze", "--python", str(runtime_python), "--strict"]
    if not allow_python_downloads:
        command.append("--no-python-downloads")
    return command


def publish(staging: Path, destination: Path, *, error: type[Exception] = UvEnvError) -> None:
    """Put a finished staging directory in place; never overwrite a winner."""
    if destination.exists():
        raise error(f"runtime destination appeared during bootstrap: {destination}")
    staging.rename(destination)
