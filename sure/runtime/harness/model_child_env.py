#!/usr/bin/env python3
"""Build a Model Python child environment without Harness Python leakage."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


def _inside(path: str, root: Path) -> bool:
    try:
        Path(path).expanduser().resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def _filtered_absolute_paths(value: str, blocked_roots: list[Path]) -> str:
    if not blocked_roots:
        return ""
    kept = []
    for entry in value.split(os.pathsep):
        path = Path(entry).expanduser()
        if not entry or not path.is_absolute():
            continue
        if not any(_inside(entry, root) for root in blocked_roots):
            kept.append(entry)
    return os.pathsep.join(kept)


def model_child_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Remove interpreter-specific state injected by the Harness wrapper."""
    env = dict(os.environ if source is None else source)
    env.pop("PYTHONHOME", None)
    python_path = env.pop("PYTHONPATH", "")
    env.pop("PYTHONEXECUTABLE", None)

    blocked_roots = []
    for key in ("SURE_HARNESS_RUNTIME_ROOT", "SURE_EVAL_CONTAINER_REPO_ROOT"):
        raw = env.get(key, "").strip()
        if raw:
            blocked_roots.append(Path(raw).expanduser().resolve())
    filtered_python_path = _filtered_absolute_paths(python_path, blocked_roots)
    if filtered_python_path:
        env["PYTHONPATH"] = filtered_python_path

    harness_root_raw = env.get("SURE_HARNESS_RUNTIME_ROOT", "").strip()
    library_path = env.get("LD_LIBRARY_PATH", "")
    if harness_root_raw and library_path:
        harness_root = Path(harness_root_raw).expanduser().resolve()
        kept = [
            entry
            for entry in library_path.split(os.pathsep)
            if entry and not _inside(entry, harness_root)
        ]
        if kept:
            env["LD_LIBRARY_PATH"] = os.pathsep.join(kept)
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env
