#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_site_policy_without_results_roots_is_a_clear_error(self) -> None:
        site_policy = Path(self.temp.name) / "site.yaml"
        site_policy.write_text(
            "schema: sure.site.policy.v1\n"
            "site_id: test-site\n"
            "policy_version: 1\n"
            "storage:\n"
            "  approved_models_roots: [/srv/models]\n"
            "  forbidden_output_roots: [/srv]\n"
            "  runtime_root: /srv/runtime\n"
            "datasets:\n"
            "  allowed_source_roots: {default: /srv/datasets}\n"
            "execution:\n"
            "  surfaces: [local]\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, {"SURE_SITE_POLICY": str(site_policy)}):
            with self.assertRaisesRegex(ValueError, "requires storage.approved_results_roots"):
                build_payload(self.args(), approved_models_root=self.models, approved_results_root=None)

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


class LocalInferSourceTests(unittest.TestCase):
    """A /sure_infer bundle (no report.jsonl) resolves as the local_infer_run source."""

    DATASET = "aishell1__unversioned"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.models = root / "models"
        model = self.models / "demo"
        model.mkdir(parents=True)
        (model / "config.yaml").write_text("task: ASR\n", encoding="utf-8")
        write_json(model / "artifacts" / "verdict.json", {"status": "success"})

        self.bundle = root / "out" / "run_a"
        predictions = self.bundle / "predictions"
        predictions.mkdir(parents=True)
        (predictions / f"{self.DATASET}.txt").write_text("sample-1\tone\nsample-2\ttwo\n", encoding="utf-8")
        write_json(predictions / f"{self.DATASET}.jsonl", {"key": "sample-1", "prediction": {"text": "one"}})
        (self.bundle / "protocol.yaml").write_text("protocol_id: standard_system\n", encoding="utf-8")
        self.status_path = self.bundle / "prediction_generation_status.json"
        write_json(
            self.status_path,
            {
                "schema": "sure.eval.prediction_generation_status.v2",
                "model_name": "demo",
                "protocol_id": "standard_system",
                "datasets": [
                    {"dataset": self.DATASET, "status": "completed", "num_expected_samples": 2, "num_generated_samples": 2}
                ],
            },
        )
        write_json(
            self.bundle / "references" / "sure_benchmark" / "jsonl" / f"{self.DATASET}.jsonl",
            {"key": "sample-1", "task": "ASR", "language": "zh"},
        )

    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            model="demo",
            datasets=[self.DATASET],
            protocol_id="standard_system",
            source_run=str(self.bundle),
        )

    def test_local_bundle_resolves_without_report_or_results_root(self) -> None:
        payload = build_payload(self.args(), approved_models_root=self.models, approved_results_root=None)

        self.assertEqual(payload["schema"], "sure.reval.approved_prediction_source.v2")
        self.assertEqual(payload["source_kind"], "local_infer_run")
        self.assertIsNone(payload["source_report"])
        self.assertEqual(payload["datasets"], [self.DATASET])
        self.assertEqual(payload["source_results_dir"], str(self.bundle.resolve()))
        self.assertIsNone(payload["source_result_relative_path"])
        self.assertFalse(payload["inference_allowed"])
        txt = self.bundle / "predictions" / f"{self.DATASET}.txt"
        triples = [[self.DATASET, hashlib.sha256(txt.read_bytes()).hexdigest(), 2]]
        expected = hashlib.sha256(json.dumps(triples, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(payload["source_report_sha256"], expected)
        self.assertEqual(len(payload["source_report_sha256"]), 64)
        self.assertEqual(payload["predictions"][0]["txt_samples"], 2)
        self.assertEqual(payload["predictions"][0]["jsonl"], str((self.bundle / "predictions" / f"{self.DATASET}.jsonl").resolve()))

    def test_dataset_that_is_not_completed_is_rejected(self) -> None:
        status = json.loads(self.status_path.read_text(encoding="utf-8"))
        status["datasets"][0]["status"] = "running"
        write_json(self.status_path, status)

        with self.assertRaisesRegex(ValueError, "not completed"):
            build_payload(self.args(), approved_models_root=self.models, approved_results_root=None)

    def test_missing_references_is_input_evidence_missing(self) -> None:
        shutil.rmtree(self.bundle / "references")

        with self.assertRaises(FileNotFoundError) as ctx:
            build_payload(self.args(), approved_models_root=self.models, approved_results_root=None)

        self.assertIn("INPUT_EVIDENCE_MISSING", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
