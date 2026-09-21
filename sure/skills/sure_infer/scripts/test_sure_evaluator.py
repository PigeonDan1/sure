#!/usr/bin/env python3
"""Tests for the vendored SUREEvaluator scoring paths.

Run directly:
    cd sure/skills/sure_infer/scripts && python test_sure_evaluator.py
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.evaluation.sure_evaluator import SUREEvaluator  # noqa: E402

STM_LINES = ["session1 1 spk1 0.0 1.0 hello world"]


def _write(directory: Path, name: str, lines: list[str]) -> str:
    path = directory / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _der(error_rate: float) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        error_rate=error_rate,
        missed_speaker_time=0.0,
        falarm_speaker_time=0.0,
        speaker_error_time=0.0,
    )


def _fake_meeteval(sessions: dict) -> types.ModuleType:
    """Stand-in for the parts of the meeteval API the evaluator uses."""
    module = types.ModuleType("meeteval")
    module.io = types.SimpleNamespace(load=lambda path: path)
    module.der = types.SimpleNamespace(dscore=lambda ref, hyp, collar=0.0: dict(sessions))
    module.wer = types.SimpleNamespace(
        cpwer=lambda ref, hyp: {},
        combine_error_rates=lambda values: types.SimpleNamespace(error_rate=0.25),
    )
    return module


class MeetevalDependencyTests(unittest.TestCase):
    """A missing dependency or an empty session set is an error, not a 0.0 score."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.ref = _write(self.tmp, "ref.stm", STM_LINES)
        self.hyp = _write(self.tmp, "hyp.stm", STM_LINES)
        self.evaluator = SUREEvaluator(language="en")

    def test_sd_without_meeteval_raises_naming_the_package(self):
        with mock.patch.dict(sys.modules, {"meeteval": None}):
            with self.assertRaises(RuntimeError) as ctx:
                self.evaluator._eval_sd(self.ref, self.hyp)
        self.assertIn("meeteval", str(ctx.exception))

    def test_sa_asr_without_meeteval_raises_naming_the_package(self):
        with mock.patch.dict(sys.modules, {"meeteval": None}):
            with self.assertRaises(RuntimeError) as ctx:
                self.evaluator._eval_sa_asr(self.ref, self.hyp)
        self.assertIn("meeteval", str(ctx.exception))

    def test_sd_without_sessions_raises(self):
        with mock.patch.dict(sys.modules, {"meeteval": _fake_meeteval({})}):
            with self.assertRaises(ValueError) as ctx:
                self.evaluator._eval_sd(self.ref, self.hyp)
        self.assertIn("session", str(ctx.exception).lower())

    def test_sa_asr_without_sessions_raises(self):
        with mock.patch.dict(sys.modules, {"meeteval": _fake_meeteval({})}):
            with self.assertRaises(ValueError) as ctx:
                self.evaluator._eval_sa_asr(self.ref, self.hyp)
        self.assertIn("session", str(ctx.exception).lower())

    def test_sd_scores_when_sessions_exist(self):
        with mock.patch.dict(sys.modules, {"meeteval": _fake_meeteval({"session1": _der(0.5)})}):
            result = self.evaluator._eval_sd(self.ref, self.hyp)
        self.assertEqual(result["num_sessions"], 1)
        self.assertAlmostEqual(result["der"], 0.5)

    def test_sa_asr_scores_when_sessions_exist(self):
        with mock.patch.dict(sys.modules, {"meeteval": _fake_meeteval({"session1": _der(0.5)})}):
            result = self.evaluator._eval_sa_asr(self.ref, self.hyp)
        self.assertEqual(result["num_sessions"], 1)
        self.assertAlmostEqual(result["der"], 0.5)
        self.assertAlmostEqual(result["cpwer"], 0.25)


if __name__ == "__main__":
    unittest.main()
