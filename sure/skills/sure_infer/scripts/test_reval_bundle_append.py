#!/usr/bin/env python3
"""Regression tests for complete /sure_reval result-bundle persistence."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from run_reval import _approved_reference_datasets_root, append_staging_bundle


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, ensure_ascii=False) + "\n")


class RevalBundleAppendTest(unittest.TestCase):
    def test_reference_root_must_be_inside_approved_nfs_source(self) -> None:
        models_root = self.root / "nfs" / "models"
        model_dir = models_root / "model"
        model_dir.mkdir(parents=True)
        source = {"model_dir": str(model_dir), "source_results_dir": str(self.source)}

        with self.assertRaisesRegex(FileNotFoundError, "INPUT_EVIDENCE_MISSING"):
            _approved_reference_datasets_root(
                source,
                approved_models_root=models_root,
                approved_results_root=self.root / "nfs" / "results",
            )

        reference_root = self.source / "references"
        (reference_root / "sure_benchmark" / "jsonl").mkdir(parents=True)
        resolved = _approved_reference_datasets_root(
            source,
            approved_models_root=models_root,
            approved_results_root=self.root / "nfs" / "results",
        )
        self.assertEqual(resolved, reference_root.resolve())

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "nfs" / "results" / "model" / "standard_system"
        self.staging = self.root / "sure" / "results" / "model" / "standard_system"
        self.source.mkdir(parents=True)
        _write(self.source / "protocol.yaml", "schema: sure.eval.protocol.v1\nprotocol_id: standard_system\n")
        _write(self.source / "predictions" / "dataset__v1.txt", "sample\tprediction\n")
        _write(
            self.source / "report.jsonl",
            json.dumps(
                {
                    "schema": "sure.eval.report.dataset_metric.v1",
                    "run": {"run_id": "approved", "protocol_id": "standard_system"},
                    "model": {"model_name": "model"},
                    "dataset": {"name": "dataset__v1", "task": "ASR", "language": "en"},
                    "prediction": {"validation": {"is_valid": True}},
                    "metric": {"name": "wer", "score": 0.2},
                    "pipeline": {"pipeline_id": "approved"},
                    "artifacts": {},
                    "status": "success",
                },
                sort_keys=True,
            )
            + "\n",
        )
        _write(self.source / "report_snapshot.md", "# approved snapshot\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _scratch(self, name: str) -> tuple[Path, dict[str, str], list[dict[str, object]]]:
        scratch = self.root / name / "scratch"
        metric_dir = scratch / "metrics" / "dataset__v1" / "wer__new_pipeline"
        sample_report = scratch / "sample_reports" / "dataset__v1" / "wer__new_pipeline.jsonl"
        _json(metric_dir / "report.json", {"pipeline_id": "new_pipeline", "score": 0.1})
        _json(metric_dir / "pipeline_description.json", {"pipeline_id": "new_pipeline", "nodes": []})
        _write(sample_report, '{"sample_id":"sample","score":0.1}\n')
        _json(scratch / "evaluation_payload.json", {"artifact": str(metric_dir / "report.json")})
        _json(scratch / "validation_payload.json", {"is_valid": True})
        _json(scratch / "prediction_source_resolved.json", {"source_kind": "approved_nfs_results"})
        _json(scratch / "prediction_reuse_manifest.json", {"enabled": True})
        _json(scratch / "source_inference_provenance.json", {"inference_executed": False})
        _json(scratch / "evaluation_route_plan.json", {"pipeline_id": "new_pipeline"})
        _json(scratch / "model_eval_manifest.json", {"evaluation_only": True})
        _json(scratch / "main_agent_run_report.json", {"evaluation_only": True})
        _write(scratch / "protocol.yaml", "protocol_id: standard_system\n")
        _write(scratch / "predictions" / "dataset__v1.txt", "sample\tprediction\n")
        _write(scratch / "report_snapshot.md", "# scratch snapshot\n")
        _write(scratch / "evaluation_runs" / "external" / "raw.txt", "raw evaluator evidence\n")
        _json(scratch / "evaluation_runs" / "external" / "artifact_manifest.json", {"external": True})
        row: dict[str, object] = {
            "schema": "sure.eval.report.dataset_metric.v1",
            "run": {"run_id": "sure_reval_record", "protocol_id": "standard_system"},
            "model": {"model_name": "model", "fingerprint": "f" * 64},
            "dataset": {"name": "dataset__v1", "task": "ASR", "language": "en"},
            "prediction": {"file": str(self.source / "predictions" / "dataset__v1.txt")},
            "metric": {"name": "wer", "score": 0.1},
            "pipeline": {
                "pipeline_id": "new_pipeline",
                "nodes": [],
                "report_path": str(metric_dir / "report.json"),
                "description_path": str(metric_dir / "pipeline_description.json"),
            },
            "artifacts": {
                "metric_artifact_dir": str(metric_dir),
                "report": str(metric_dir / "report.json"),
                "pipeline_description": str(metric_dir / "pipeline_description.json"),
                "sample_report": str(sample_report),
            },
            "status": "success",
            "reval": {
                "schema": "sure.reval.report_append.v2",
                "record_id": "a" * 64,
                "identity": {"pipeline_id": "new_pipeline"},
                "source_report_sha256": "placeholder",
                "inference_executed": False,
            },
        }
        _write(scratch / "report.jsonl", json.dumps(row) + "\n")
        artifacts = {
            "prediction_source_resolved": str(scratch / "prediction_source_resolved.json"),
            "prediction_reuse_manifest": str(scratch / "prediction_reuse_manifest.json"),
            "source_inference_provenance": str(scratch / "source_inference_provenance.json"),
            "evaluation_route_plan": str(scratch / "evaluation_route_plan.json"),
            "validation_payload": str(scratch / "validation_payload.json"),
            "evaluation_payload": str(scratch / "evaluation_payload.json"),
            "protocol": str(scratch / "protocol.yaml"),
            "report_jsonl": str(scratch / "report.jsonl"),
            "report_snapshot": str(scratch / "report_snapshot.md"),
            "predictions_dir": str(scratch / "predictions"),
            "metrics_dir": str(scratch / "metrics"),
            "sample_reports_dir": str(scratch / "sample_reports"),
            "model_eval_manifest": str(scratch / "model_eval_manifest.json"),
            "main_agent_run_report": str(scratch / "main_agent_run_report.json"),
        }
        return scratch, artifacts, [row]

    def _append(self, scratch_name: str) -> dict[str, object]:
        scratch, artifacts, rows = self._scratch(scratch_name)
        source_hash = hashlib.sha256((self.source / "report.jsonl").read_bytes()).hexdigest()
        reval = rows[0]["reval"]
        assert isinstance(reval, dict)
        reval["source_report_sha256"] = source_hash
        return append_staging_bundle(
            source_result_dir=self.source,
            staging_result_dir=self.staging,
            scratch_root=scratch,
            scratch_artifacts=artifacts,
            rows=rows,
        )

    def test_first_append_persists_complete_bundle_and_second_is_noop(self) -> None:
        first = self._append("run_one")
        self.assertTrue(first["base_materialized"])
        self.assertTrue(first["batch_materialized"])
        self.assertEqual(first["appended_record_ids"], ["a" * 64])
        self.assertFalse(first["idempotent"])
        self.assertEqual(
            (self.staging / "predictions" / "dataset__v1.txt").read_bytes(),
            (self.source / "predictions" / "dataset__v1.txt").read_bytes(),
        )
        batch = Path(str(first["batch_dir"]))
        manifest = json.loads((batch / "artifact_manifest.json").read_text(encoding="utf-8"))
        listed = {item["path"] for item in manifest["files"]}
        self.assertIn("metrics/dataset__v1/wer__new_pipeline/report.json", listed)
        self.assertIn("sample_reports/dataset__v1/wer__new_pipeline.jsonl", listed)
        self.assertIn("evaluation_runs/external/raw.txt", listed)
        self.assertIn("evaluation_runs/external/artifact_manifest.json", listed)
        rows = [json.loads(line) for line in (self.staging / "report.jsonl").read_text().splitlines()]
        appended = rows[-1]
        self.assertEqual(
            appended["pipeline"]["report_path"],
            f"evaluation_runs/{first['batch_id']}/metrics/dataset__v1/wer__new_pipeline/report.json",
        )
        self.assertTrue((self.staging / appended["pipeline"]["report_path"]).is_file())
        report_hash = first["staging_report_sha256"]
        snapshot_hash = first["staging_snapshot_sha256"]

        second = self._append("run_two")
        self.assertFalse(second["base_materialized"])
        self.assertFalse(second["batch_materialized"])
        self.assertEqual(second["appended_record_ids"], [])
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["staging_report_sha256"], report_hash)
        self.assertEqual(second["staging_snapshot_sha256"], snapshot_hash)

    def test_changed_approved_baseline_is_rejected(self) -> None:
        self._append("run_one")
        _write(self.staging / "protocol.yaml", "protocol_id: tampered\n")
        with self.assertRaisesRegex(ValueError, "differs from approved NFS artifact"):
            self._append("run_two")

    def test_changed_persisted_batch_is_rejected(self) -> None:
        first = self._append("run_one")
        _write(Path(str(first["batch_dir"])) / "metrics" / "dataset__v1" / "wer__new_pipeline" / "report.json", "{}\n")
        with self.assertRaisesRegex(ValueError, "hash or size changed"):
            self._append("run_two")


if __name__ == "__main__":
    unittest.main()
