#!/usr/bin/env python3
"""Legacy compatibility entrypoint for the shared SURE-TRANS validator."""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


_TARGET = Path("sure/canonical/shared/trans-validator/scripts/check_artifact.py")


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
    raise RuntimeError("canonical shared trans validator is unavailable")


_target = _target_path()
if str(_target.parent) not in sys.path:
    sys.path.insert(0, str(_target.parent))
_namespace = runpy.run_path(str(_target), run_name=__name__)
for _name, _value in _namespace.items():
    if _name not in {"__name__", "__file__", "__cached__", "__loader__", "__package__"}:
        globals()[_name] = _value
