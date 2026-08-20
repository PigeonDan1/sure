#!/usr/bin/env python3
"""Resolve the common Harness Runtime contract exported by skill pre-start hooks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


class HarnessRuntimeBindingError(ValueError):
    """The exported common Harness Runtime identity is absent or inconsistent."""


def _required(env: Mapping[str, str], key: str) -> str:
    value = str(env.get(key) or "").strip()
    if not value:
        raise HarnessRuntimeBindingError(f"HARNESS_RUNTIME_NOT_READY: {key} is not set")
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_binding(
    *,
    python_value: str,
    runtime_id: str,
    lock_sha256: str,
    manifest_value: str,
) -> dict[str, Any]:
    python = Path(python_value).expanduser().resolve()
    manifest_path = Path(manifest_value).expanduser().resolve()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise HarnessRuntimeBindingError(f"HARNESS_RUNTIME_NOT_READY: Python is not executable: {python}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessRuntimeBindingError(f"HARNESS_RUNTIME_NOT_READY: invalid manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != "sure.harness.runtime.manifest.v1":
        raise HarnessRuntimeBindingError("HARNESS_RUNTIME_NOT_READY: unsupported runtime manifest")
    if manifest.get("runtime_id") != runtime_id or manifest.get("lock_sha256") != lock_sha256:
        raise HarnessRuntimeBindingError("HARNESS_RUNTIME_NOT_READY: exported identity disagrees with manifest")
    runtime_root = manifest_path.parent.resolve()
    if not _inside(python, runtime_root):
        raise HarnessRuntimeBindingError("HARNESS_RUNTIME_NOT_READY: Python escapes runtime root")
    return {
        "schema": "sure.harness.runtime.binding.v1",
        "runtime_id": runtime_id,
        "runtime_type": "harness_python",
        "python_executable": str(python),
        "python_version": manifest.get("python_version"),
        "python_abi": manifest.get("python_abi"),
        "harness_version": manifest.get("harness_version"),
        "lock_sha256": lock_sha256,
        "manifest_path": str(manifest_path),
        "runtime_root": str(runtime_root),
        "materialization": manifest.get("materialization"),
        "materialization_version": manifest.get("materialization_version"),
        "install_log": manifest.get("install_log"),
    }


def load_harness_runtime(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = os.environ if env is None else env
    return _validate_binding(
        python_value=_required(values, "HARNESS_PYTHON_BIN"),
        runtime_id=_required(values, "SURE_HARNESS_RUNTIME_ID"),
        lock_sha256=_required(values, "SURE_HARNESS_LOCK_SHA256"),
        manifest_value=_required(values, "SURE_HARNESS_MANIFEST_PATH"),
    )


def harness_runtime_from_eval_input(eval_input: Mapping[str, Any]) -> dict[str, Any]:
    runtime = eval_input.get("runtime")
    binding = runtime.get("harness_runtime") if isinstance(runtime, Mapping) else None
    if not isinstance(binding, Mapping) or binding.get("schema") != "sure.harness.runtime.binding.v1":
        raise HarnessRuntimeBindingError(
            "HARNESS_RUNTIME_NOT_READY: eval input does not contain a Harness Runtime binding"
        )
    return _validate_binding(
        python_value=str(binding.get("python_executable") or ""),
        runtime_id=str(binding.get("runtime_id") or ""),
        lock_sha256=str(binding.get("lock_sha256") or ""),
        manifest_value=str(binding.get("manifest_path") or ""),
    )
