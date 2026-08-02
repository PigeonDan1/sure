#!/usr/bin/env python3
"""Regression test for check_match_task.py task_type domain validation.

check_match_task.py only checked that match_source was present for a matched
candidate; it never checked task_type against the allowed value set. An
illegal task_type survived the MATCH_TASK gate (unit 2) and was only caught
three units later by check_model_input.py's LOAD_MODEL_INPUT gate (unit 5).
This pins the fix: an illegal task_type must be rejected here, at unit 2.

Run directly:
    cd sure/skills/sure_feed/scripts && python test_check_match_task.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_match_task.py"


def run_gate(produces: Path, run_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--run-dir", str(run_dir), "--produces", str(produces)],
        capture_output=True,
        text=True,
    )


class TaskTypeDomain(unittest.TestCase):
    def test_illegal_task_type_is_rejected_here(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            p = run_dir / "match_task_result.json"
            p.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "model_id": "x/y",
                                "match": {
                                    "matched": True,
                                    "match_source": "tasks",
                                    "task_type": "not_a_real_task",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            r = run_gate(p, run_dir)
            self.assertEqual(r.returncode, 1)
            self.assertIn("task_type", r.stderr)

    def test_legal_task_type_still_passes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            p = run_dir / "match_task_result.json"
            p.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "model_id": "x/y",
                                "match": {
                                    "matched": True,
                                    "match_source": "tasks",
                                    "task_type": "asr",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            r = run_gate(p, run_dir)
            self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_missing_task_type_still_passes(self) -> None:
        """task_type is optional at this unit; absence is not itself an error."""
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            p = run_dir / "match_task_result.json"
            p.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "model_id": "x/y",
                                "match": {"matched": True, "match_source": "tasks"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            r = run_gate(p, run_dir)
            self.assertEqual(r.returncode, 0, msg=r.stderr)


if __name__ == "__main__":
    unittest.main()
