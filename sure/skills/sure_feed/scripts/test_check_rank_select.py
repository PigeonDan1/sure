#!/usr/bin/env python3
"""Regression test for check_rank_select.py evidence checking.

The RANK_AND_SELECT gate accepted --run-dir and never opened it, so it judged
rank_select_result.json entirely by rank_select_result.json: a selection could
name a model that SYNTHESIZE_MODEL_INPUT never produced. It also accepted
`"score": true`, because bool is a subclass of int in Python. This pins both:
the selection is cross-checked against the run directory's
model_input_result.json, and a boolean score is not a number.

Run directly:
    cd sure/skills/sure_feed/scripts && python test_check_rank_select.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_rank_select.py"


def seed_run(root: Path, selected: list[dict], synthesized_ids: list[str] | None) -> tuple[Path, Path]:
    """Write a run directory with a selection and, optionally, its upstream evidence."""
    run_dir = root / "run"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    if synthesized_ids is not None:
        (artifacts / "model_input_result.json").write_text(
            json.dumps({"model_inputs": [{"model_id": mid} for mid in synthesized_ids]}),
            encoding="utf-8",
        )
    produces = artifacts / "rank_select_result.json"
    produces.write_text(json.dumps({"selected": selected}), encoding="utf-8")
    return run_dir, produces


def run_gate(run_dir: Path, produces: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--run-dir", str(run_dir), "--produces", str(produces)],
        capture_output=True,
        text=True,
    )


class RankSelectEvidence(unittest.TestCase):
    def test_selection_backed_by_the_run_directory_passes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run_dir, produces = seed_run(
                Path(d),
                [{"model_id": "x/y", "repo": "https://huggingface.co/x/y", "score": 1.5}],
                ["x/y"],
            )
            r = run_gate(run_dir, produces)
            self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_model_never_synthesized_is_rejected(self) -> None:
        """A selection cannot name a model that SYNTHESIZE_MODEL_INPUT never produced."""
        with tempfile.TemporaryDirectory() as d:
            run_dir, produces = seed_run(
                Path(d),
                [{"model_id": "made/up", "repo": "https://huggingface.co/made/up", "score": 1.5}],
                ["x/y"],
            )
            r = run_gate(run_dir, produces)
            self.assertEqual(r.returncode, 1)
            self.assertIn("made/up", r.stderr)
            self.assertIn("model_input_result.json", r.stderr)

    def test_missing_upstream_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run_dir, produces = seed_run(
                Path(d),
                [{"model_id": "x/y", "repo": "https://huggingface.co/x/y", "score": 1.5}],
                None,
            )
            r = run_gate(run_dir, produces)
            self.assertEqual(r.returncode, 1)
            self.assertIn("model_input_result.json", r.stderr)

    def test_evidence_under_artifacts_debug_is_found(self) -> None:
        """The hook writes state-machine artifacts under artifacts/debug/."""
        with tempfile.TemporaryDirectory() as d:
            run_dir, produces = seed_run(
                Path(d),
                [{"model_id": "x/y", "repo": "https://huggingface.co/x/y", "score": 1.5}],
                None,
            )
            debug = run_dir / "artifacts" / "debug"
            debug.mkdir()
            (debug / "model_input_result.json").write_text(
                json.dumps({"model_inputs": [{"model_id": "x/y"}]}), encoding="utf-8"
            )
            r = run_gate(run_dir, produces)
            self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_boolean_score_is_not_a_number(self) -> None:
        """bool is a subclass of int, so isinstance(True, int) let `score: true` pass."""
        with tempfile.TemporaryDirectory() as d:
            run_dir, produces = seed_run(
                Path(d),
                [{"model_id": "x/y", "repo": "https://huggingface.co/x/y", "score": True}],
                ["x/y"],
            )
            r = run_gate(run_dir, produces)
            self.assertEqual(r.returncode, 1)
            self.assertIn("score", r.stderr)
            self.assertIn("True", r.stderr)


if __name__ == "__main__":
    unittest.main()
