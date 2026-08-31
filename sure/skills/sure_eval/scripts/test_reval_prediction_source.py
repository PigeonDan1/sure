#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from resolve_model_dir import resolve_approved_model_identity
from resolve_prediction_source import build_payload


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


class RevalPredictionSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.models = root / "models"
        self.results = root / "results"
        self.model = self.models / "demo"
        self.model.mkdir(parents=True)
        (self.model / "config.yaml").write_text("task: ASR\n", encoding="utf-8")
        write_json(self.model / "artifacts" / "verdict.json", {"status": "success"})

        result = self.results / "demo" / "approved-run"
        predictions = result / "predictions"
        predictions.mkdir(parents=True)
        (result / "protocol.yaml").write_text("protocol_id: standard_system\n", encoding="utf-8")
        write_json(
            result / "report.jsonl",
            {
                "dataset": {"name": "aishell1__v1.0.2"},
                "run": {"protocol_id": "standard_system"},
                "model": {"model_name": "demo"},
                "prediction": {"file": "aishell1__v1.0.2.txt"},
            },
        )
        (predictions / "aishell1__v1.0.2.txt").write_text("sample-1\tprediction\n", encoding="utf-8")

    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            model="demo",
            datasets=["aishell1__v1.0.2"],
            protocol_id="standard_system",
        )

    def test_resolves_approved_predictions_without_deployment_runtime_artifacts(self) -> None:
        payload = build_payload(
            self.args(),
            approved_models_root=self.models,
            approved_results_root=self.results,
        )

        self.assertEqual(payload["model_name"], "demo")
        self.assertFalse(payload["inference_allowed"])
        self.assertFalse((self.model / "artifacts" / "deployment_ready.json").exists())

    def test_result_dir_that_never_finished_evaluating_is_named_in_the_error(self) -> None:
        # What a run that died in [5/5] leaves behind: predictions, no protocol
        # and no report. It used to be skipped silently, so the error read as if
        # the directory were not there at all.
        crashed = self.results / "demo" / "crashed-run"
        (crashed / "predictions").mkdir(parents=True)
        (crashed / "predictions" / "aishell1__v1.0.2.txt").write_text("sample-1\tp\n", encoding="utf-8")
        args = self.args()
        args.datasets = ["other_ds__v1.0.0"]

        with self.assertRaises(FileNotFoundError) as ctx:
            build_payload(args, approved_models_root=self.models, approved_results_root=self.results)

        message = str(ctx.exception)
        self.assertIn("crashed-run", message)
        self.assertIn("protocol.yaml", message)
        self.assertIn("Re-run the evaluation", message)

    def test_identity_rejects_non_successful_verdict(self) -> None:
        write_json(self.model / "artifacts" / "verdict.json", {"status": "partial"})

        identity = resolve_approved_model_identity("demo", approved_root=self.models)

        self.assertFalse(identity["ok"])
        self.assertIn("not successful", identity["identity_error"])
        with self.assertRaisesRegex(ValueError, "successful verdict"):
            build_payload(
                self.args(),
                approved_models_root=self.models,
                approved_results_root=self.results,
            )

    def test_identity_prefers_artifacts_verdict_over_legacy_top_level(self) -> None:
        write_json(self.model / "verdict.json", {"status": "success"})
        write_json(self.model / "artifacts" / "verdict.json", {"status": "failed"})

        identity = resolve_approved_model_identity("demo", approved_root=self.models)

        self.assertFalse(identity["ok"])
        self.assertEqual(identity["verdict_status"], "failed")
        self.assertEqual(identity["verdict_path"], str((self.model / "artifacts" / "verdict.json").resolve()))

    def test_identity_falls_back_to_legacy_top_level_verdict(self) -> None:
        (self.model / "artifacts" / "verdict.json").unlink()
        write_json(self.model / "verdict.json", {"status": "success"})

        identity = resolve_approved_model_identity("demo", approved_root=self.models)

        self.assertTrue(identity["ok"])
        self.assertEqual(identity["verdict_path"], str((self.model / "verdict.json").resolve()))

    def test_identity_requires_config(self) -> None:
        (self.model / "config.yaml").unlink()

        identity = resolve_approved_model_identity("demo", approved_root=self.models)

        self.assertFalse(identity["ok"])
        self.assertEqual(identity["identity_error"], "approved model config.yaml is missing")


if __name__ == "__main__":
    unittest.main()
