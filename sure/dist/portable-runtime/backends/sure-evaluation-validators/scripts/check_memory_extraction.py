#!/usr/bin/env python3
"""Thin wrapper: the logic lives in sure/runtime/memory/. Hooks may only spawn scripts under this skill's scripts/."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime"))

from memory import proposals  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(proposals.main(sys.argv[1:]))
