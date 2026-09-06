#!/usr/bin/env python3
"""Gate script for the run_report unit of /sure_eval (invoked with --profile eval).

The check itself lives in the inference skill package; this wrapper exists so
the hook can run a gate script from this package directory.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

try:
    from sure.runtime.resource_locator import resolve_backend_script
except ModuleNotFoundError:
    for _parent in Path(__file__).resolve().parents:
        if (_parent / "sure" / "runtime" / "resource_locator.py").is_file():
            sys.path.insert(0, str(_parent))
            break
    from sure.runtime.resource_locator import resolve_backend_script

TARGET = resolve_backend_script("sure.eval.validate_run_report", "sure_infer", "check_run_report.py")

if __name__ == "__main__":
    sys.argv[0] = str(TARGET)
    runpy.run_path(str(TARGET), run_name="__main__")
