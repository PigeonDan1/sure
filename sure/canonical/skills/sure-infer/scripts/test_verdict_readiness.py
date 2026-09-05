#!/usr/bin/env python3
"""A sealed bundle's verdict must say the model passed, not merely exist."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import resolve_model_dir  # noqa: E402
from deployment_binding import DeploymentBindingError, load_deployment_binding  # noqa: E402


class VerdictReadinessTest(unittest.TestCase):
    def _model_dir(self, root: Path, verdict: dict) -> Path:
        model_dir = root / "demo"
        (model_dir / "artifacts").mkdir(parents=True)
        (model_dir / "artifacts" / "verdict.json").write_text(
            json.dumps(verdict) + chr(10), encoding="utf-8"
        )
        return model_dir

    def test_a_passing_verdict_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = self._model_dir(Path(temporary), {"status": "success"})
            self.assertTrue(resolve_model_dir.verdict_is_ready(model_dir))

    def test_a_failed_verdict_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = self._model_dir(Path(temporary), {"status": "failed"})
            self.assertFalse(resolve_model_dir.verdict_is_ready(model_dir))

    def test_an_unreadable_verdict_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary) / "demo"
            (model_dir / "artifacts").mkdir(parents=True)
            (model_dir / "artifacts" / "verdict.json").write_text("not json", encoding="utf-8")
            self.assertFalse(resolve_model_dir.verdict_is_ready(model_dir))

    def test_a_missing_verdict_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertFalse(resolve_model_dir.verdict_is_ready(Path(temporary) / "demo"))


class BindingVerdictTest(unittest.TestCase):
    def test_a_bundle_whose_verdict_failed_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary) / "demo"
            artifacts = model_dir / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "verdict.json").write_text(
                json.dumps({"status": "failed"}) + chr(10), encoding="utf-8"
            )
            with self.assertRaises(DeploymentBindingError) as raised:
                load_deployment_binding(model_dir, "demo")
            self.assertIn("verdict", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
