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
    """Remove Harness interpreter state without dropping the model's own paths."""
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
    return env
