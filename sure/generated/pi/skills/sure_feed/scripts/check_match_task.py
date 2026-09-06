#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical shared feed validator."""
from __future__ import annotations

import os
import runpy
from pathlib import Path


_TARGET = Path("sure/canonical/shared/feed-validator/scripts/check_match_task.py")


def _target_path() -> Path:
    roots = []
    configured = os.environ.get("SURE_REPOSITORY_ROOT", "").strip()
    if configured:
        roots.append(Path(configured))
    roots.extend(Path(__file__).resolve().parents)
    for root in roots:
        candidate = root / _TARGET
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise RuntimeError("canonical shared feed validator is unavailable")


_namespace = runpy.run_path(str(_target_path()), run_name=__name__)
for _name, _value in _namespace.items():
    if _name not in {"__name__", "__file__", "__cached__", "__loader__", "__package__"}:
        globals()[_name] = _value
