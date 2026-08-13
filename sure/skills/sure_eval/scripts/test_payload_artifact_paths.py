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
    """The vc runner invokes evaluate_predictions.py with a repo-relative
    --run-dir, so payload artifact paths must never depend on the cwd the
    writer happened to run from (check_run_report resolves relative paths
    against the run root)."""

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
        prediction_source = external / "ds__v1.txt"
        prediction_source.write_text("utt1\thello\n", encoding="utf-8")
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
        metric_dir = run_dir.resolve() / "metrics" / "ds__v1" / "cer"
        self.assertEqual(Path(artifacts["metric_artifact_dir"]), metric_dir)
        self.assertEqual(Path(artifacts["report"]), metric_dir / "report.json")
        self.assertEqual(Path(artifacts["pipeline_description"]), metric_dir / "pipeline_description.json")
        self.assertEqual(
            Path(artifacts["sample_report"]),
            run_dir.resolve() / "sample_reports" / "ds__v1" / "cer.jsonl",
        )
        self.assertTrue(Path(artifacts["prediction_file"]).is_absolute())
        pipeline = payload["results"][0]["pipeline"]
        self.assertEqual(Path(pipeline["report_path"]), metric_dir / "report.json")
        self.assertEqual(Path(pipeline["description_path"]), metric_dir / "pipeline_description.json")

    def test_gate_locates_metric_artifacts_written_from_relative_run_dir(self) -> None:
        run_dir = Path("sure/results/model/standard_system/run-1")
        self._write_artifacts(run_dir)
        errors = check_run_report._validate_completed_artifacts(run_dir.resolve())
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
