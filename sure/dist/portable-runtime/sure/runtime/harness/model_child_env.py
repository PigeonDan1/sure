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


def model_child_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Remove interpreter-specific state injected by the Harness wrapper."""
    env = dict(os.environ if source is None else source)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONEXECUTABLE", None)

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
