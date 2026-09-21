#!/usr/bin/env python3
"""Materialize and verify the versioned SURE Harness Python runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "runtime" / "uvenv.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from sure.runtime.uvenv import (  # noqa: E402
    child_environment,
    exclusive_lock,
    probe as probe_python,
    publish,
    runtime_python_relative,
    sha256_file,
    sync_command,
    uv_binary,
    venv_command,
)


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]
DEFAULT_RUNTIME_ROOT = REPO_ROOT / "sure" / ".runtime" / "harness"
MANIFEST_NAME = "runtime-manifest.json"


class HarnessRuntimeError(RuntimeError):
    """The common Harness Runtime could not be prepared or verified."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessRuntimeError(f"invalid runtime definition {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HarnessRuntimeError(f"runtime definition must be a JSON object: {path}")
    return payload


def _load_spec() -> tuple[dict[str, Any], Path, str, str]:
    spec = _read_json(SPEC_DIR / "runtime.json")
    if spec.get("schema") != "sure.harness.runtime.spec.v1":
        raise HarnessRuntimeError("unsupported Harness Runtime definition schema")
    python_version = str(spec.get("python") or "")
    if not python_version or python_version.count(".") != 1:
        raise HarnessRuntimeError("runtime definition must pin a Python major.minor ABI")
    lock_path = SPEC_DIR / str(spec.get("lock_file") or "")
    if not lock_path.is_file():
        raise HarnessRuntimeError(f"Harness dependency lock is missing: {lock_path}")
    lock_sha256 = sha256_file(lock_path)
    harness_version = str(spec.get("harness_version") or "").strip()
    if not harness_version:
        raise HarnessRuntimeError("runtime definition must declare harness_version")
    try:
        materialization_version = int(spec.get("materialization_version") or 0)
    except (TypeError, ValueError):
        # A value int() cannot read is as unusable as a missing one, and only a
        # HarnessRuntimeError reaches the launcher as a message instead of a traceback.
        materialization_version = 0
    if materialization_version < 1:
        raise HarnessRuntimeError("runtime definition must declare materialization_version")
    runtime_id = (
        f"sure-harness-{harness_version}-m{materialization_version}"
        f"-py{python_version.replace('.', '')}-{lock_sha256[:12]}"
    )
    return spec, lock_path, lock_sha256, runtime_id


def _python_path(runtime_dir: Path) -> Path:
    return runtime_dir / runtime_python_relative()


def _manifest_path(runtime_dir: Path) -> Path:
    return runtime_dir / MANIFEST_NAME


def _probe(python: Path, required_imports: list[str]) -> dict[str, Any]:
    modules = ",".join(required_imports)
    code = (
        "import importlib,json,platform,sys,sysconfig;"
        f"mods={modules!r}.split(',') if {modules!r} else [];"
        "[importlib.import_module(name) for name in mods];"
        "print(json.dumps({'executable':sys.executable,'version':platform.python_version(),"
        "'abi':sysconfig.get_config_var('SOABI') or '',"
        "'prefix':sys.prefix,'base_prefix':sys.base_prefix}))"
    )
    completed = subprocess.run(
        [str(python), "-s", "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=child_environment(),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise HarnessRuntimeError(f"Harness Runtime import probe failed: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessRuntimeError("Harness Runtime import probe returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HarnessRuntimeError("Harness Runtime import probe returned a non-object")
    return payload


def _verified_contract(
    runtime_dir: Path,
    spec: dict[str, Any],
    lock_sha256: str,
    runtime_id: str,
) -> dict[str, Any]:
    manifest_path = _manifest_path(runtime_dir)
    if not manifest_path.is_file():
        raise HarnessRuntimeError(f"Harness Runtime manifest is missing: {manifest_path}")
    manifest = _read_json(manifest_path)
    if manifest.get("schema") != "sure.harness.runtime.manifest.v1":
        raise HarnessRuntimeError("unsupported Harness Runtime manifest schema")
    expected = {
        "runtime_id": runtime_id,
        "lock_sha256": lock_sha256,
        "harness_version": spec["harness_version"],
        "python_abi": f"cp{str(spec['python']).replace('.', '')}",
        "materialization_version": spec["materialization_version"],
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise HarnessRuntimeError(f"Harness Runtime manifest {key} mismatch")
    required_imports = [str(value) for value in spec.get("required_imports", [])]
    python = _python_path(runtime_dir)
    if not python.is_file() or not os.access(python, os.X_OK):
        raise HarnessRuntimeError(f"Harness Runtime Python is missing or not executable: {python}")
    # The manifest records which base interpreter the venv was built on. Reading
    # it back is the whole point of recording it: a venv is a handful of paths
    # pointing at an interpreter this directory does not own, and one that has
    # been replaced or upgraded underneath it no longer matches the lock that was
    # resolved for it. A mismatch reaches resolve_runtime, which quarantines the
    # directory and rebuilds rather than trusting it.
    identity = probe_python(python, error=HarnessRuntimeError)
    if identity["base_python_sha256"] != manifest.get("base_python_sha256"):
        raise HarnessRuntimeError("Harness Runtime base_python_sha256 mismatch")
    probe = _probe(python, required_imports)
    if not str(probe.get("version") or "").startswith(f"{spec['python']}."):
        raise HarnessRuntimeError(
            f"Harness Runtime Python ABI mismatch: expected {spec['python']}, got {probe.get('version')}"
        )
    contract = dict(manifest)
    contract.update(
        {
            "status": "ready",
            # Not python.resolve(): in a venv bin/python is a symlink to the base
            # interpreter, and following it leaves the venv and its packages behind.
            "python_executable": str(_python_path(runtime_dir.resolve())),
            "runtime_root": str(runtime_dir.resolve()),
            "manifest_path": str(manifest_path.resolve()),
            "spec_path": str((SPEC_DIR / "runtime.json").resolve()),
            "lock_path": str((SPEC_DIR / str(spec["lock_file"])).resolve()),
            "probe": probe,
        }
    )
    return contract


def _run_logged(command: list[str], env: dict[str, str], log: TextIO, *, timeout: int) -> None:
    redacted = [part.split("@", 1)[-1] if "://" in part and "@" in part else part for part in command]
    log.write(f"$ {json.dumps(redacted)}\n")
    log.flush()
    completed = subprocess.run(
        command,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise HarnessRuntimeError(f"command failed with exit code {completed.returncode}: {command[0]}")


def _build_runtime(
    runtime_root: Path,
    runtime_dir: Path,
    spec: dict[str, Any],
    lock_path: Path,
    lock_sha256: str,
    runtime_id: str,
) -> dict[str, Any]:
    uv = uv_binary(
        error=HarnessRuntimeError,
        message=(
            "uv is required to prepare the locked Harness Runtime; "
            "set SURE_UV_BIN to an existing uv executable"
        ),
    )
    logs_dir = runtime_root / "logs"
    cache_dir = runtime_root / "cache"
    for path in (logs_dir, cache_dir):
        path.mkdir(parents=True, exist_ok=True)
    max_attempts = max(1, min(2, int(spec.get("max_prepare_attempts") or 2)))
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        staging = Path(tempfile.mkdtemp(prefix=f".{runtime_id}.attempt-{attempt}-", dir=runtime_root))
        log_path = logs_dir / f"bootstrap-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-attempt-{attempt}.log"
        env = child_environment()
        env.update(
            {
                "UV_CACHE_DIR": str(cache_dir),
                "UV_LINK_MODE": "copy",
            }
        )
        try:
            with log_path.open("w", encoding="utf-8") as log:
                log.write(
                    json.dumps(
                        {
                            "runtime_type": "harness_python",
                            "runtime_id": runtime_id,
                            "attempt": attempt,
                            "package_source": "configured uv index (credentials omitted)",
                            "lock_sha256": lock_sha256,
                            "cache_dir": str(cache_dir),
                            "started_at": _utc_now(),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                # uv fetches its own CPython when the host has none. That download is
                # uv's, hash-checked by uv, and it is the reason a plain PC needs no
                # Python of its own; the interpreter it picked is recorded below.
                _run_logged(
                    venv_command(uv, staging, python=str(spec["python"]), allow_python_downloads=True),
                    env,
                    log,
                    timeout=600,
                )
                runtime_python = _python_path(staging)
                sync_extra = ["--link-mode", "copy", "--cache-dir", str(cache_dir)]
                if attempt == 2:
                    sync_extra.append("--refresh")
                _run_logged(
                    sync_command(
                        uv,
                        runtime_python,
                        lock_path,
                        allow_python_downloads=True,
                        extra=sync_extra,
                    ),
                    env,
                    log,
                    timeout=600,
                )
                identity = probe_python(runtime_python, error=HarnessRuntimeError)
                imports = _probe(runtime_python, [str(value) for value in spec.get("required_imports", [])])
                manifest = {
                    "schema": "sure.harness.runtime.manifest.v1",
                    "runtime_id": runtime_id,
                    "runtime_type": "harness_python",
                    "harness_version": spec["harness_version"],
                    "python_abi": f"cp{str(spec['python']).replace('.', '')}",
                    "python_version": imports["version"],
                    "lock_sha256": lock_sha256,
                    "required_imports": spec.get("required_imports", []),
                    "package_source": "configured uv index (credentials omitted)",
                    "cache_dir": str(cache_dir.resolve()),
                    "install_log": str(log_path.resolve()),
                    "prepare_attempt": attempt,
                    "prepared_at": _utc_now(),
                    "materialization": "uv_venv",
                    "materialization_version": spec["materialization_version"],
                    "base_python_sha256": identity["base_python_sha256"],
                }
                _manifest_path(staging).write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                publish(staging, runtime_dir, error=HarnessRuntimeError)
                return _verified_contract(runtime_dir, spec, lock_sha256, runtime_id)
        except (HarnessRuntimeError, OSError, subprocess.SubprocessError) as exc:
            errors.append(f"attempt {attempt}: {exc}; log={log_path}")
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    raise HarnessRuntimeError("Harness Runtime preparation failed after bounded attempts: " + "; ".join(errors))


def _prune_superseded(runtime_root: Path, runtime_id: str) -> None:
    """Drop the runtimes under this root that the current runtime_id replaced.

    runtime_id carries the harness version, the materialization version and the
    lock hash, so every bump of any of them materializes a new directory beside
    the old one; nothing collected the old one and the root grew a whole venv
    per bump. Only well-formed runtime directories are in scope: the
    `.{runtime_id}.invalid-*` quarantines and `.{runtime_id}.attempt-*` staging
    trees are left alone, the first because it is the only record of why a
    runtime was rejected and the second because it belongs to a bootstrap in
    flight. Best effort: a directory some already-running process still holds
    open must not fail a bootstrap that has otherwise succeeded.
    """
    for entry in runtime_root.iterdir():
        if entry.name.startswith(".") or entry.name == runtime_id or not entry.is_dir():
            continue
        try:
            manifest = json.loads((entry / MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(manifest, dict) and manifest.get("schema") == "sure.harness.runtime.manifest.v1":
            shutil.rmtree(entry, ignore_errors=True)


def resolve_runtime(runtime_root: Path, *, repair: bool = True, prune: bool = True) -> dict[str, Any]:
    spec, lock_path, lock_sha256, runtime_id = _load_spec()
    runtime_root = runtime_root.expanduser().resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_dir = runtime_root / runtime_id
    with exclusive_lock(runtime_root / ".bootstrap.lock"):
        try:
            return _verified_contract(runtime_dir, spec, lock_sha256, runtime_id)
        except HarnessRuntimeError:
            if not repair:
                raise
        # Quarantine runs under the same lock as the rebuild: two sessions that both
        # find a stale runtime must not race on this rename.
        if runtime_dir.exists():
            quarantine = runtime_root / f".{runtime_id}.invalid-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
            runtime_dir.rename(quarantine)
        contract = _build_runtime(runtime_root, runtime_dir, spec, lock_path, lock_sha256, runtime_id)
        # Under the same lock as the build: no other resolve can be verifying or
        # publishing a sibling directory while this one is being reclaimed.
        if prune:
            _prune_superseded(runtime_root, runtime_id)
        return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--check", action="store_true", help="Verify only; do not prepare or repair")
    parser.add_argument("--json", action="store_true", help="Print the resolved runtime contract as JSON")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the runtime directories this runtime_id supersedes instead of reclaiming them",
    )
    args = parser.parse_args()
    try:
        contract = resolve_runtime(Path(args.runtime_root), repair=not args.check, prune=not args.keep)
    except HarnessRuntimeError as exc:
        print(f"HARNESS_RUNTIME_NOT_READY: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(contract, sort_keys=True) if args.json else contract["python_executable"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
