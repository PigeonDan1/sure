#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import check_run_report
import evaluate_predictions


class PayloadArtifactPathTests(unittest.TestCase):
    """Run artifact references must remain valid across mount namespaces."""

    def setUp(self) -> None:
        self._previous_cwd = Path.cwd()
        self._temporary = tempfile.TemporaryDirectory()
        os.chdir(self._temporary.name)

    def tearDown(self) -> None:
        os.chdir(self._previous_cwd)
        self._temporary.cleanup()

    def _write_artifacts(self, run_dir: Path) -> dict:
        external = Path("external_artifacts")
        external.mkdir(parents=True, exist_ok=True)
        sample_source = external / "sample.jsonl"
        sample_source.write_text('{"key": "utt1"}\n', encoding="utf-8")
        prediction_source = run_dir / "predictions" / "ds__v1.txt"
        prediction_source.parent.mkdir(parents=True, exist_ok=True)
        prediction_source.write_text("utt1\thello\n", encoding="utf-8")
        prediction_source.with_suffix(".jsonl").write_text(
            '{"key": "utt1", "normalized_prediction": "hello"}\n',
            encoding="utf-8",
        )
        result = {
            "schema": "sure.eval.payload.dataset_metric.v2",
            "dataset": "ds__v1",
            "metric": "cer",
            "pipeline_id": "p1",
            "result": {"score": 0.1},
            "prediction_path": str(prediction_source),
            "sample_report_path": str(sample_source),
        }
        evaluate_predictions._write_run_artifacts(
            run_dir=run_dir,
            tool_name="model",
            protocol_id="standard_system",
            model_dir=None,
            payload={"schema": "sure.eval.payload.v2", "results": [dict(result)]},
            results=[result],
        )
        return json.loads((run_dir / "evaluation_payload.json").read_text(encoding="utf-8"))

    def test_artifact_paths_do_not_depend_on_writer_cwd(self) -> None:
        run_dir = Path("sure/results/model/standard_system/run-1")
        payload = self._write_artifacts(run_dir)
        artifacts = payload["results"][0]["artifacts"]
        metric_dir = Path("metrics/ds__v1/cer")
        self.assertEqual(Path(artifacts["metric_artifact_dir"]), metric_dir)
        self.assertEqual(Path(artifacts["report"]), metric_dir / "report.json")
        self.assertEqual(Path(artifacts["pipeline_description"]), metric_dir / "pipeline_description.json")
        self.assertEqual(
            Path(artifacts["sample_report"]),
            Path("sample_reports/ds__v1/cer.jsonl"),
        )
        self.assertEqual(Path(artifacts["prediction_file"]), Path("predictions/ds__v1.txt"))
        self.assertEqual(Path(payload["results"][0]["inputs"]["prediction_path"]), Path("predictions/ds__v1.txt"))
        pipeline = payload["results"][0]["pipeline"]
        self.assertEqual(Path(pipeline["report_path"]), metric_dir / "report.json")
        self.assertEqual(Path(pipeline["description_path"]), metric_dir / "pipeline_description.json")

    def test_gate_locates_metric_artifacts_after_run_directory_moves(self) -> None:
        container_run_dir = Path("container/sure-output")
        self._write_artifacts(container_run_dir)
        host_run_dir = Path("host/results/model/standard_system/run-1")
        host_run_dir.parent.mkdir(parents=True, exist_ok=True)
        container_run_dir.rename(host_run_dir)
        errors = check_run_report._validate_completed_artifacts(host_run_dir.resolve())
        missing = [
            error
            for error in errors
            if "missing metric report" in error
            or "missing pipeline description" in error
            or "missing sample report" in error
        ]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
