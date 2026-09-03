#!/usr/bin/env python3
"""Gate script for the assessment unit of /sure_eval.

The check itself lives in the inference skill package; this wrapper exists so
the hook can run a gate script from this package directory.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1].parent / "sure_infer" / "scripts" / "check_assessment.py"

if __name__ == "__main__":
    sys.argv[0] = str(TARGET)
    runpy.run_path(str(TARGET), run_name="__main__")
