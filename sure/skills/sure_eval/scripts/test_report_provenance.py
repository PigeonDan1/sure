#!/usr/bin/env python3
"""Tests: dataset source provenance reaches payload/report rows; RPS falls back
to source_dataset_name.

Run directly:
    cd sure/skills/sure_eval/scripts && python test_report_provenance.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_predictions as ep  # noqa: E402
from sure_eval.reports.sota_manager import SOTAManager  # noqa: E402


def write_jsonl(path: Path) -> Path:
    row = {
        "key": "utt1",
        "path": "utt1.wav",
        "target": "你好",
        "task": "ASR",
        "language": "zh",
        "dataset": "demo_ds__v1.0.2",
        "metadata": {
            "source": "aispeech_ds_pool",
            "source_dataset_root": "/srv/sure/datasets/group/store/ds_pool/demo_ds",
            "source_dataset_name": "demo_ds",
            "version_id": "v1.0.2",
        },
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


class DatasetSourceFieldsTests(unittest.TestCase):
    def test_reads_source_fields_from_first_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = write_jsonl(Path(tmp) / "demo_ds__v1.0.2.jsonl")
            fields = ep._dataset_source_fields(str(jsonl))
        self.assertEqual(fields["source_dataset_name"], "demo_ds")
        self.assertEqual(fields["version_id"], "v1.0.2")
        self.assertTrue(fields["source_root"].endswith("ds_pool/demo_ds"))

    def test_missing_metadata_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "x.jsonl"
            jsonl.write_text('{"key": "utt1"}\n', encoding="utf-8")
            self.assertEqual(ep._dataset_source_fields(str(jsonl)), {})
        self.assertEqual(ep._dataset_source_fields(None), {})
        self.assertEqual(ep._dataset_source_fields("/no/such/file.jsonl"), {})

    def test_non_dict_json_first_line_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "x.jsonl"
            jsonl.write_text("[1, 2, 3]\n", encoding="utf-8")
            self.assertEqual(ep._dataset_source_fields(str(jsonl)), {})

    def test_invalid_utf8_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "x.jsonl"
            jsonl.write_bytes(b"\xff\xfe\x00bad")
            self.assertEqual(ep._dataset_source_fields(str(jsonl)), {})


class PayloadAndReportRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.jsonl = write_jsonl(Path(self._tmp.name) / "demo_ds__v1.0.2.jsonl")
        self.result = {
            "dataset": "demo_ds__v1.0.2",
            "jsonl_path": str(self.jsonl),
            "prediction_path": "predictions/demo_ds__v1.0.2.txt",
            "task": "ASR",
            "language": "zh",
            "metric": "cer",
            "score": 0.05,
            "num_samples": 1,
        }

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_payload_row_carries_source_object(self) -> None:
        row = ep._dataset_metric_row(self.result)
        self.assertEqual(row["source"]["source_dataset_name"], "demo_ds")
        self.assertEqual(row["source"]["version_id"], "v1.0.2")

    def test_report_row_dataset_carries_source_fields(self) -> None:
        payload_row = ep._dataset_metric_row(self.result)
        report_row = ep._standard_report_row_v1(
            row=payload_row,
            validation={},
            run_id="run1",
            protocol_id="proto1",
            model_dir=None,
            tool_name="demo_tool",
        )
        dataset = report_row["dataset"]
        self.assertEqual(dataset["name"], "demo_ds__v1.0.2")
        self.assertEqual(dataset["source_dataset_name"], "demo_ds")
        self.assertEqual(dataset["version_id"], "v1.0.2")
        self.assertTrue(dataset["source_root"].endswith("ds_pool/demo_ds"))


class SotaFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        sota_file = Path(self._tmp.name) / "sota_baseline.yaml"
        sota_file.write_text(
            "demo_ds:\n  metric: cer\n  score: 5.0\n  higher_is_better: false\n  sota_model: X\n",
            encoding="utf-8",
        )
        self.manager = SOTAManager(sota_file)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_full_id_misses_then_source_name_hits(self) -> None:
        baseline = self.manager.get_baseline("demo_ds__v1.0.2", fallback_names=["demo_ds"])
        self.assertIsNotNone(baseline)
        self.assertEqual(baseline.metric, "cer")
        rps = self.manager.calculate_rps("demo_ds__v1.0.2", 0.05, fallback_names=["demo_ds"])
        self.assertIsInstance(rps, float)

    def test_no_fallback_keeps_missing_baseline_contract(self) -> None:
        result = self.manager.calculate_rps("demo_ds__v1.0.2", 0.05)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], "missing_baseline")

    def test_get_metric_follows_fallback_chain(self) -> None:
        self.assertIsNone(self.manager.get_metric("demo_ds__v1.0.2"))
        self.assertEqual(
            self.manager.get_metric("demo_ds__v1.0.2", fallback_names=["demo_ds"]), "cer"
        )


if __name__ == "__main__":
    unittest.main()
