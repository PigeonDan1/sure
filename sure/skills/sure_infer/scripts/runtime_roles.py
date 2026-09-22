"""Helpers for distinguishing approved Python runtime roles."""

from __future__ import annotations

import shutil
from pathlib import Path


def resolve_executable(value: str | Path) -> Path | None:
    """Resolve a command without following a venv's final Python symlink."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        resolved = shutil.which(str(candidate))
        if not resolved:
            return None
        candidate = Path(resolved)
    if not candidate.exists():
        return None
    return candidate.parent.resolve() / candidate.name


def same_runtime_executable(left: str | Path, right: str | Path) -> bool:
    """Return whether two executable paths belong to the same runtime role."""
    resolved_left = resolve_executable(left)
    resolved_right = resolve_executable(right)
    return resolved_left is not None and resolved_right is not None and resolved_left == resolved_right
