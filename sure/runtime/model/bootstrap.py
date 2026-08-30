#!/usr/bin/env python3
"""Materialize and verify content-addressed Model Python runtimes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "sure.model.runtime.manifest.v1"
MATERIALIZATION_VERSION = 1
MANIFEST_NAME = "runtime-manifest.json"
LOCK_NAME = "requirements.lock"
PACKAGES_NAME = "installed-packages.txt"


class ModelRuntimeError(RuntimeError):
    """The selected Model Python runtime is missing or invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelRuntimeError(f"invalid Model Runtime manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelRuntimeError(f"Model Runtime manifest must be a JSON object: {path}")
    return value


def _probe(python: Path) -> dict[str, str]:
    code = (
        "import hashlib,json,platform,sys,sysconfig;"
        "base=__import__('pathlib').Path(sys._base_executable).resolve();"
        "print(json.dumps({'python_version':platform.python_version(),"
        "'python_abi':sysconfig.get_config_var('SOABI') or '',"
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
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise ModelRuntimeError(f"Model Python probe failed: {detail}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ModelRuntimeError("Model Python probe returned invalid JSON") from exc
    if not isinstance(value, dict) or not all(isinstance(item, str) and item for item in value.values()):
        raise ModelRuntimeError("Model Python probe returned incomplete identity data")
    return value


def _runtime_id(lock_sha256: str, probe: dict[str, str]) -> str:
    identity = {
        "backend": "uv",
        "base_python_sha256": probe["base_python_sha256"],
        "lock_sha256": lock_sha256,
        "materialization": "uv_venv",
        "materialization_version": MATERIALIZATION_VERSION,
        "python_abi": probe["python_abi"],
        "python_platform": probe["python_platform"],
        "python_version": probe["python_version"],
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sure-model-python-v{MATERIALIZATION_VERSION}-{digest[:24]}"


def _uv_binary(explicit: str | None = None) -> str:
    candidate = (explicit or os.environ.get("SURE_UV_BIN", "").strip() or shutil.which("uv") or "")
    if not candidate or not Path(candidate).is_file():
        raise ModelRuntimeError("uv is required to materialize package=none Model Python runtimes")
    return str(Path(candidate).resolve())


def _run(command: list[str], *, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise ModelRuntimeError(f"Model Runtime command failed ({command[1]}): {detail}")
    return completed


def _expected_manifest(
    *,
    runtime_id: str,
    lock_sha256: str,
    probe: dict[str, str],
    installed_packages_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "runtime_id": runtime_id,
        "runtime_type": "model_python",
        "backend": "uv",
        "materialization": "uv_venv",
        "materialization_version": MATERIALIZATION_VERSION,
        "python_executable": "bin/python",
        "python_version": probe["python_version"],
        "python_abi": probe["python_abi"],
        "python_platform": probe["python_platform"],
        "base_python_sha256": probe["base_python_sha256"],
        "lock_file": LOCK_NAME,
        "lock_sha256": lock_sha256,
        "installed_packages_file": PACKAGES_NAME,
        "installed_packages_sha256": installed_packages_sha256,
    }


def verify_runtime(runtime_root: Path, expected: dict[str, Any]) -> dict[str, Any]:
    """Resolve a portable manifest against one site's runtime root."""
    if expected.get("schema") != SCHEMA:
        raise ModelRuntimeError("unsupported Model Runtime manifest schema")
    runtime_id = str(expected.get("runtime_id") or "")
    if not re.fullmatch(r"sure-model-python-v1-[0-9a-f]{24}", runtime_id):
        raise ModelRuntimeError("invalid Model Runtime ID")
    fixed_fields = {
        "runtime_type": "model_python",
        "backend": "uv",
        "materialization": "uv_venv",
        "materialization_version": MATERIALIZATION_VERSION,
        "python_executable": "bin/python",
        "lock_file": LOCK_NAME,
        "installed_packages_file": PACKAGES_NAME,
    }
    for key, value in fixed_fields.items():
        if expected.get(key) != value:
            raise ModelRuntimeError(f"invalid Model Runtime {key}")
    root = runtime_root.expanduser().resolve()
    runtime_dir = (root / runtime_id).resolve()
    try:
        runtime_dir.relative_to(root)
    except ValueError as exc:
        raise ModelRuntimeError("Model Runtime ID escapes the configured runtime root") from exc
    manifest_path = runtime_dir / MANIFEST_NAME
    actual = _read_json(manifest_path)
    if actual != expected:
        raise ModelRuntimeError("site Model Runtime manifest disagrees with the sealed model binding")
    lock_path = runtime_dir / LOCK_NAME
    packages_path = runtime_dir / PACKAGES_NAME
    python = runtime_dir / str(expected["python_executable"])
    if not lock_path.is_file() or sha256_file(lock_path) != expected.get("lock_sha256"):
        raise ModelRuntimeError("site Model Runtime lock hash mismatch")
    if not packages_path.is_file() or sha256_file(packages_path) != expected.get("installed_packages_sha256"):
        raise ModelRuntimeError("site Model Runtime package inventory hash mismatch")
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ModelRuntimeError(f"site Model Python is missing or not executable: {python}")
    probe = _probe(python)
    for key in ("python_version", "python_abi", "python_platform", "base_python_sha256"):
        if probe.get(key) != expected.get(key):
            raise ModelRuntimeError(f"site Model Runtime {key} mismatch")
    return {
        **actual,
        "runtime_root": str(runtime_dir),
        "manifest_path": str(manifest_path),
        "python_executable_resolved": str(python),
        "manifest_sha256": manifest_sha256(actual),
        "probe": probe,
    }


def materialize_runtime(
    *,
    runtime_root: Path,
    source_python: Path,
    lock_path: Path,
    uv_bin: str | None = None,
) -> dict[str, Any]:
    """Build one immutable uv environment and return its verified contract."""
    source_python = source_python.expanduser().resolve()
    lock_path = lock_path.expanduser().resolve()
    if not source_python.is_file() or not os.access(source_python, os.X_OK):
        raise ModelRuntimeError(f"selected source Python is missing or not executable: {source_python}")
    if not lock_path.is_file():
        raise ModelRuntimeError(f"locked requirements file is missing: {lock_path}")
    source_probe = _probe(source_python)
    lock_sha256 = sha256_file(lock_path)
    runtime_id = _runtime_id(lock_sha256, source_probe)
    root = runtime_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    runtime_dir = root / runtime_id
    lock_file = root / f".{runtime_id}.lock"

    with lock_file.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if runtime_dir.exists():
            return verify_runtime(root, _read_json(runtime_dir / MANIFEST_NAME))

        staging = Path(tempfile.mkdtemp(prefix=f".{runtime_id}.", dir=root))
        try:
            uv = _uv_binary(uv_bin)
            cache_dir = root / ".uv-cache"
            cache_dir.mkdir(exist_ok=True)
            env = os.environ.copy()
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            env["UV_CACHE_DIR"] = str(cache_dir)
            _run(
                [uv, "venv", "--no-project", "--no-python-downloads", "--python", source_probe["base_python"], str(staging)],
                env=env,
                timeout=180,
            )
            runtime_python = staging / "bin" / "python"
            _run(
                [
                    uv,
                    "pip",
                    "sync",
                    "--python",
                    str(runtime_python),
                    "--require-hashes",
                    "--strict",
                    "--allow-empty-requirements",
                    "--no-python-downloads",
                    str(lock_path),
                ],
                env=env,
                timeout=1800,
            )
            frozen = _run(
                [uv, "pip", "freeze", "--python", str(runtime_python), "--strict", "--no-python-downloads"],
                env=env,
                timeout=180,
            ).stdout
            packages_path = staging / PACKAGES_NAME
            packages_path.write_text(frozen, encoding="utf-8")
            shutil.copy2(lock_path, staging / LOCK_NAME)
            runtime_probe = _probe(runtime_python)
            for key in ("python_version", "python_abi", "python_platform", "base_python_sha256"):
                if runtime_probe[key] != source_probe[key]:
                    raise ModelRuntimeError(f"materialized Model Runtime {key} differs from source Python")
            manifest = _expected_manifest(
                runtime_id=runtime_id,
                lock_sha256=lock_sha256,
                probe=runtime_probe,
                installed_packages_sha256=sha256_file(packages_path),
            )
            (staging / MANIFEST_NAME).write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            staging.rename(runtime_dir)
        except (OSError, subprocess.SubprocessError):
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return verify_runtime(root, _read_json(runtime_dir / MANIFEST_NAME))
