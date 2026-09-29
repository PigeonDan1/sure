#!/usr/bin/env python3
"""Regression tests for the deterministic sure_finish manifest envelope.

SURE-20260929-003: run 015331 reached the terminal unit but sure_finish kept
demanding envelope fields (schema_version, run_id, ...) one at a time while the
onboard deployment_ready schema rejected any added field — the onboard skill
never materialized the finish manifest the harness contract requires. The
finalize helper now writes it; these tests pin that envelope against the
sure_finish validator's requirements.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from finalize_model_bundle import finish_manifest

ENVELOPE_FIELDS = (
    "schema_version",
    "run_id",
    "skill_name",
    "status",
    "created_at",
    "inputs",
    "outputs",
    "validation",
)


class FinishManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = Path(self._tmp.name) / "run"
        self.run_dir.mkdir(parents=True)

    def write_run_record(self, record: dict) -> None:
        (self.run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")

    def resolved(self) -> dict:
        return {
            "model_id": "example/diarizer",
            "task_type": "sd",
            "model_input_path": "sure/handoffs/example/model_input.yaml",
            "device": "cuda",
            "package_profile": "none",
        }

    def deployment(self) -> dict:
        return {
            "status": "local_only",
            "model_name": "example__diarizer",
            "package_profile": "none",
            "bundle_identity_sha256": "a" * 64,
        }

    def test_writes_envelope_satisfying_sure_finish(self) -> None:
        self.write_run_record({"runId": "20260929-015331-b570d4c4", "skillName": "sure_onboard"})
        manifest = finish_manifest(self.run_dir, self.resolved(), self.deployment())
        written = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(written, manifest)
        for key in ENVELOPE_FIELDS:
            self.assertIn(key, written)
        self.assertEqual(written["run_id"], "20260929-015331-b570d4c4")
        self.assertEqual(written["skill_name"], "sure_onboard")
        self.assertEqual(written["status"], "success")
        self.assertIsInstance(written["schema_version"], str)
        self.assertTrue(written["schema_version"].strip())
        self.assertTrue(written["created_at"].strip())
        # sure_finish's isStringRecord: every inputs/outputs/validation value
        # must be a string.
        for section in ("inputs", "outputs", "validation"):
            self.assertIsInstance(written[section], dict)
            for value in written[section].values():
                self.assertIsInstance(value, str)
        self.assertEqual(written["outputs"]["deployment_ready"], "artifacts/deployment_ready.json")

    def test_missing_run_identity_fails_the_finalize(self) -> None:
        self.write_run_record({"skillName": "sure_onboard"})
        with self.assertRaises(ValueError) as ctx:
            finish_manifest(self.run_dir, self.resolved(), self.deployment())
        self.assertIn("runId", str(ctx.exception))
        self.assertFalse((self.run_dir / "manifest.json").exists())

    def test_missing_skill_name_fails_the_finalize(self) -> None:
        self.write_run_record({"runId": "20260929-015331-b570d4c4"})
        with self.assertRaises(ValueError) as ctx:
            finish_manifest(self.run_dir, self.resolved(), self.deployment())
        self.assertIn("skillName", str(ctx.exception))

    def test_created_at_is_iso_parseable(self) -> None:
        from datetime import datetime

        self.write_run_record({"runId": "r-1", "skillName": "sure_onboard"})
        manifest = finish_manifest(self.run_dir, self.resolved(), self.deployment())
        self.assertIsNotNone(datetime.fromisoformat(manifest["created_at"]))


if __name__ == "__main__":
    unittest.main()
