#!/usr/bin/env python3
"""Regression coverage for the harness-side KWS inference/evaluation flow."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_predictions as evaluation  # noqa: E402
import generate_predictions_via_server as generation  # noqa: E402
import resolve_eval_input  # noqa: E402
from sure_eval.datasets import source_resolver  # noqa: E402
from sure_eval.datasets.dataset_manager import DatasetManager  # noqa: E402
from validate_prediction_files import validate_prediction_file  # noqa: E402


def _manager(root: Path) -> DatasetManager:
    manager = object.__new__(DatasetManager)
    manager.config = SimpleNamespace(datasets=SimpleNamespace(definitions={}), get_dataset=lambda name: None)
    manager.data_dir = root / "data"
    manager.sure_dir = manager.data_dir / "sure_benchmark"
    manager.jsonl_dir = manager.sure_dir / "jsonl"
    manager.jsonl_dir.mkdir(parents=True, exist_ok=True)
    manager.dataset_source_key = "default"
    return manager


def _write_kws_source(root: Path, *, include_negative: bool = True) -> Path:
    source = root / "wake_words"
    source.mkdir(parents=True)
    positive = source / "positive.wav"
    negative = source / "negative.wav"
    positive.write_bytes(b"RIFF-positive")
    negative.write_bytes(b"RIFF-negative")
    rows = [
        {
            "key": "positive",
            "audio": positive.name,
            "task": "KWS",
            "language": "zh",
            "keywords": ["你好问问", "嗨小问"],
            "expected": "detect",
            "expected_keyword": "嗨小问",
            "duration": 1.25,
            "threshold": 0.2,
        }
    ]
    if include_negative:
        rows.append(
            {
                "key": "negative",
                "audio": negative.name,
                "task": "KWS",
                "language": "zh",
                "keywords": ["你好问问", "嗨小问"],
                "expected": "reject",
                "duration": 2.5,
            }
        )
    (source / "sample.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return source


class KwsSourceProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_root = self.root / "sources"
        self.source_root.mkdir()
        self.env = mock.patch.dict(os.environ, {source_resolver.SOURCE_ROOT_ENV: str(self.source_root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_projects_explicit_kws_source_for_det_scoring(self) -> None:
        source = _write_kws_source(self.source_root)
        ref = source_resolver.resolve_site_source_entry(str(source))
        self.assertEqual(source_resolver.read_source_task(ref), "KWS")

        manager = _manager(self.root)
        output = manager._convert_source_root_to_jsonl(ref)
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([row["task"] for row in rows], ["KWS", "KWS"])
        self.assertEqual([row["expected_detected"] for row in rows], [True, False])
        self.assertEqual(rows[0]["expected_keyword"], "嗨小问")
        self.assertEqual(rows[1]["expected_keyword"], None)
        self.assertEqual(rows[1]["duration"], 2.5)
        manifest = json.loads(
            (manager.sure_dir / "wake_words" / "dataset_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["default_projection"], "kws_wakeword_v1")

    def test_projects_kws_source_with_wav_header_duration(self) -> None:
        source = self.source_root / "wake_words_from_header"
        source.mkdir()
        for name, frames in (("positive.wav", 8000), ("negative.wav", 16000)):
            with wave.open(str(source / name), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\0\0" * frames)
        rows = [
            {
                "key": "positive",
                "audio": "positive.wav",
                "task": "KWS",
                "keywords": ["hello"],
                "expected": "detect",
                "expected_keyword": "hello",
            },
            {
                "key": "negative",
                "audio": "negative.wav",
                "task": "KWS",
                "keywords": ["hello"],
                "expected": "reject",
            },
        ]
        (source / "sample.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

        ref = source_resolver.resolve_site_source_entry(str(source))
        output = _manager(self.root)._convert_source_root_to_jsonl(ref)
        projected = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([row["duration"] for row in projected], [0.5, 1.0])

    def test_sample_manifest_task_precedes_dataset_metadata_task_type(self) -> None:
        source = _write_kws_source(self.source_root)
        (source / "ds.jsonl").write_text(
            json.dumps({"task_type": "ASR"}) + "\n",
            encoding="utf-8",
        )
        ref = source_resolver.resolve_site_source_entry(str(source))

        self.assertEqual(source_resolver.read_source_task(ref), "KWS")

    def test_rejects_a_kws_source_without_negative_samples(self) -> None:
        source = _write_kws_source(self.source_root, include_negative=False)
        ref = source_resolver.resolve_site_source_entry(str(source))
        with self.assertRaisesRegex(ValueError, "positive and one negative"):
            _manager(self.root)._convert_source_root_to_jsonl(ref)

    def test_rejects_a_kws_model_with_an_asr_dataset(self) -> None:
        with self.assertRaisesRegex(resolve_eval_input.EvalInputError, "Task mismatch"):
            resolve_eval_input._check_task_compatibility(
                {"name": "wake-model", "declared_task": "KWS"},
                [{"name": "speech", "task": "ASR"}],
            )


class KwsGenerationContractTests(unittest.TestCase):
    def test_materializes_per_sample_keywords_and_threshold(self) -> None:
        arguments = generation._build_tool_arguments(
            repo_root=Path("/repo"),
            sample={"key": "positive", "keywords": ["你好问问", "嗨小问"], "threshold": 0.2},
            task="KWS",
            language="zh",
            argument_name="audio_path",
            audio_path=Path("/audio/positive.wav"),
            output_audio_dir=Path("/unused"),
        )
        self.assertEqual(
            arguments,
            {
                "audio_path": "/audio/positive.wav",
                "keywords": "你好问问,嗨小问",
                "threshold": 0.2,
            },
        )

    def test_normalizes_negative_detection_without_empty_tsv_projection(self) -> None:
        projection, prediction = generation._normalize_prediction_payload(
            {"detected": "false", "keyword": None, "score": 0.0}, task="KWS"
        )
        self.assertFalse(prediction["detected"])
        self.assertEqual(prediction["keyword"], None)
        self.assertTrue(projection)
        self.assertEqual(json.loads(projection), prediction)

    def test_rejects_ambiguous_or_incomplete_kws_outputs(self) -> None:
        for payload in (
            {"detected": "not sure", "keyword": None, "score": 0.2},
            {"detected": True, "keyword": None, "score": 0.9},
            {"detected": True, "keyword": "hello", "score": None},
            {"detected": False, "keyword": None, "score": None},
            {"detected": False, "keyword": None, "score": 2.0},
            "0.5",
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    generation._normalize_prediction_payload(payload, task="KWS")


class _EvaluationManager:
    def __init__(self, reference: Path) -> None:
        self.reference = reference

    def normalize_dataset_name(self, name: str) -> str:
        return name

    def get_jsonl_path(self, _name: str) -> Path:
        return self.reference


class _SotaManager:
    def get_metric(self, _name: str) -> str:
        return "accuracy"

    def calculate_rps(self, _name: str, _score: float, **_kwargs: object) -> float:
        return 1.0


class KwsPredictionAndEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reference = self.root / "references" / "wake_words.jsonl"
        self.reference.parent.mkdir()
        rows = [
            {
                "key": "positive",
                "task": "KWS",
                "language": "any",
                "keywords": ["hello"],
                "expected": "detect",
                "expected_detected": True,
                "expected_keyword": "hello",
                "duration": 1.0,
            },
            {
                "key": "negative",
                "task": "KWS",
                "language": "any",
                "keywords": ["hello"],
                "expected": "reject",
                "expected_detected": False,
                "expected_keyword": None,
                "duration": 2.0,
            },
        ]
        self.reference.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        self.prediction = self.root / "predictions" / "wake_words.txt"
        self.prediction.parent.mkdir()
        structured = self.prediction.with_suffix(".jsonl")
        predictions = [
            {"key": "positive", "task": "KWS", "prediction": {"detected": True, "keyword": "hello", "score": 0.9}},
            {"key": "negative", "task": "KWS", "prediction": {"detected": False, "keyword": None, "score": 0.0}},
        ]
        projections = [
            json.dumps(row["prediction"], sort_keys=True, separators=(",", ":")) for row in predictions
        ]
        self.prediction.write_text(
            "".join(f"{row['key']}\t{projection}\n" for row, projection in zip(predictions, projections)),
            encoding="utf-8",
        )
        for row, projection in zip(predictions, projections):
            row["normalized_prediction"] = projection
        structured.write_text(
            "".join(json.dumps(row) + "\n" for row in predictions), encoding="utf-8"
        )

    def test_prediction_validator_requires_the_structured_kws_contract(self) -> None:
        manager = _EvaluationManager(self.reference)
        result = validate_prediction_file(manager, "wake_words", self.prediction, True)
        self.assertTrue(result["is_valid"], result)

        self.prediction.with_suffix(".jsonl").unlink()
        result = validate_prediction_file(manager, "wake_words", self.prediction, True)
        self.assertFalse(result["is_valid"])
        self.assertEqual(result["structured_missing_keys"], ["negative", "positive"])

    def test_prediction_validator_rejects_missing_kws_score(self) -> None:
        structured = self.prediction.with_suffix(".jsonl")
        rows = [
            {
                "key": "positive",
                "task": "KWS",
                "prediction": {"detected": True, "keyword": "hello", "score": 0.9},
                "normalized_prediction": '{"detected":true,"keyword":"hello","score":0.9}',
            },
            {
                "key": "negative",
                "task": "KWS",
                "prediction": {"detected": False, "keyword": None, "score": None},
                "normalized_prediction": '{"detected":false,"keyword":null,"score":null}',
            },
        ]
        structured.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        self.prediction.write_text(
            "positive\t{\"detected\":true,\"keyword\":\"hello\",\"score\":0.9}\n"
            "negative\t{\"detected\":false,\"keyword\":null,\"score\":null}\n",
            encoding="utf-8",
        )

        result = validate_prediction_file(_EvaluationManager(self.reference), "wake_words", self.prediction, True)

        self.assertFalse(result["is_valid"])
        self.assertEqual(result["contract_violation_keys"], ["negative"])



    def test_external_bridge_uses_the_canonical_kws_route_and_preserves_det_metrics(self) -> None:
        pipeline_id = "kws.any.accuracy.conversion_kws_sure_json_to_samples_v1.wekws_det_v1"
        pipeline = {
            "pipeline_id": pipeline_id,
            "metric": "accuracy",
            "required_roles": ["reference_jsonl", "sample_output"],
            "nodes": [{"node_id": "scoring/wekws_det"}],
        }
        captured: dict[str, object] = {}

        def run_external(*, request: dict[str, object], **_kwargs: object) -> dict[str, object]:
            captured.update(request)
            outputs = json.loads(Path(str(request["sample_output"])).read_text(encoding="utf-8"))
            self.assertEqual([row["key"] for row in outputs], ["positive", "negative"])
            report = {
                "score": 1.0,
                "details": {
                    "results": {
                        "accuracy": {"score": 1.0},
                        "false_reject_rate": {"score": 0.0},
                        "false_alarm_rate": {"score": 0.0},
                        "false_alarm_per_hour": {"score": 0.0},
                        "det_curve": {"details": {"points": []}},
                    }
                },
            }
            return {
                "summary": {"metric": "accuracy", "score": 1.0, "pipeline_id": pipeline_id},
                "pipeline": pipeline,
                "report": report,
            }

        with (
            mock.patch.object(evaluation, "_describe_external_pipeline", return_value=pipeline),
            mock.patch.object(evaluation, "_run_external_pipeline", side_effect=run_external),
            mock.patch.object(evaluation, "_evaluation_runtime_binding", return_value={"runtime_id": "test"}),
        ):
            result = evaluation.evaluate_structured_prediction_file_external(
                _EvaluationManager(self.reference),
                _SotaManager(),
                "wake_words",
                self.prediction,
                engine_source="submodule",
                engine_root=Path("/engine"),
                external_runs_dir=self.root / "external",
                device="cpu",
                cache_dir=None,
                timeout=30,
                metric_override="accuracy",
                task_override="KWS",
            )

        self.assertEqual(captured["reference_jsonl"], str(self.reference.resolve()))
        self.assertEqual(result["pipeline_id"], pipeline_id)
        self.assertEqual(result["details"]["report"]["details"]["results"]["false_alarm_per_hour"]["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
