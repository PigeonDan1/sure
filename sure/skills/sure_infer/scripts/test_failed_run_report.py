#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_run_report


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class FailedRunReportTests(unittest.TestCase):
    def test_pre_submit_smoke_failure_can_be_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            artifacts = run_dir / "artifacts"
            report = artifacts / "main_agent_run_report.json"
            write_json(
                artifacts / "smoke_test_result.json",
                {"smoke_passed": False, "exit_code": 41, "failures": ["Harness import failed"]},
            )
            write_json(
                artifacts / "assessment_report.json",
                {"status": "failed", "anomaly_detected": True, "user_confirmed": True},
            )
            write_json(
                report,
                {
                    "run_id": "failed-run",
                    "timestamp": "now",
                    "task_type": "evaluate_existing_model",
                    "goal": "bounded evaluation",
                    "selected_datasets": ["dataset__v1"],
                    "executed_steps": ["execution_readiness", "smoke_test"],
                    "status": "failed",
                    "report_persisted": True,
                    "execution_path_actual": "blocked_before_submit",
                    "execution": {
                        "requested": "local",
                        "actual": "not_submitted",
                        "path_actual": "blocked_before_submit",
                        "failure_class": "smoke_test_failed",
                    },
                    "next_action": "Repair the classified runtime and rerun from smoke_test.",
                },
            )
            with patch.object(
                sys,
                "argv",
                [
                    "check_run_report.py",
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(report),
                ],
            ):
                self.assertEqual(check_run_report.main(), 0)

    def test_success_report_cannot_contradict_a_failed_execution_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            artifacts = run_dir / "artifacts"
            report = artifacts / "main_agent_run_report.json"
            write_json(
                artifacts / "execution_result.json",
                {"job_status": "failed", "exit_code": 1, "execution_path": "local_docker"},
            )
            write_json(
                report,
                {
                    "run_id": "contradicting-run",
                    "timestamp": "now",
                    "task_type": "evaluate_existing_model",
                    "goal": "bounded evaluation",
                    "selected_datasets": ["dataset__v1"],
                    "executed_steps": ["execution_surface", "execute_wait"],
                    "status": "success",
                    "report_persisted": True,
                    "execution_path_actual": "local_docker",
                },
            )
            stderr = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "check_run_report.py",
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(report),
                ],
            ):
                with contextlib.redirect_stderr(stderr):
                    exit_code = check_run_report.main()

        self.assertEqual(exit_code, 1)
        # Not "could not locate a run artifact root": the contradiction is the
        # first thing the gate has to say, otherwise a run whose artifacts happen
        # to be in place is reported as a success.
        self.assertIn("execution_result.json", stderr.getvalue())
        self.assertIn("exit_code", stderr.getvalue())


class InferProfileTests(unittest.TestCase):
    DATASET = "demo__v1"

    def _write_product(self, root: Path, *, references: bool = True) -> None:
        (root / "predictions").mkdir(parents=True)
        (root / "predictions" / f"{self.DATASET}.txt").write_text("k0\tp0\nk1\tp1\n", encoding="utf-8")
        write_json(
            root / "prediction_generation_status.json",
            {
                "schema": "sure.eval.prediction_generation_status.v2",
                "datasets": [
                    {"dataset": self.DATASET, "status": "completed", "num_expected_samples": 2, "num_generated_samples": 2}
                ],
            },
        )
        (root / "protocol.yaml").write_text("schema: sure.eval.inference_protocol.v1\n", encoding="utf-8")
        if references:
            references_dir = root / "references" / "sure_benchmark" / "jsonl"
            references_dir.mkdir(parents=True)
            (references_dir / f"{self.DATASET}.jsonl").write_text('{"key": "k0"}\n', encoding="utf-8")

    def _run(self, temporary: str, *, references: bool) -> tuple[int, str]:
        run_dir = Path(temporary) / "run"
        artifacts = run_dir / "artifacts"
        product = Path(temporary) / "product"
        self._write_product(product, references=references)
        write_json(
            artifacts / "execution_result.json",
            {
                "job_status": "succeeded",
                "exit_code": 0,
                "execution_path": "local_docker",
                "product_dir": str(product),
                "datasets": [{"dataset": self.DATASET, "expected": 2, "generated": 2, "valid": 2}],
            },
        )
        report = artifacts / "main_agent_run_report.json"
        write_json(
            report,
            {
                "run_id": "infer-run",
                "timestamp": "now",
                "task_type": "evaluate_existing_model",
                "goal": "bounded inference",
                "selected_datasets": [self.DATASET],
                "executed_steps": ["dataset_scope", "execute_inference"],
                "status": "success",
                "report_persisted": True,
                "execution_path_actual": "local_docker",
            },
        )
        stderr = io.StringIO()
        argv = ["check_run_report.py", "--run-dir", str(run_dir), "--produces", str(report), "--profile", "infer"]
        with patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr):
            code = check_run_report.main()
        return code, stderr.getvalue()

    def test_infer_profile_accepts_an_inference_only_product(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            code, stderr = self._run(temporary, references=True)
        self.assertEqual(code, 0, stderr)

    def test_infer_profile_requires_the_reference_projections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            code, stderr = self._run(temporary, references=False)
        self.assertEqual(code, 1)
        self.assertIn("references/sure_benchmark/jsonl", stderr.replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()
