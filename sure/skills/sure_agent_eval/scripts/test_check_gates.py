#!/usr/bin/env python3
"""Tests for the /sure_agent_eval gate scripts (check_agent_*.py).

Run directly:
    cd sure/skills/sure_agent_eval/scripts && python3 -m unittest test_check_gates.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_agent_eval_report  # noqa: E402
import check_agent_execution  # noqa: E402
import check_agent_run_report  # noqa: E402

DATASET = "mini_s2tt__unversioned"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def make_spec(product_dir: Path) -> dict:
    return {
        "schema": "sure.agent_eval.spec_resolved.v1",
        "run_id": "run_test",
        "created_at": "2026-09-10T00:00:00Z",
        "agent": {
            "name": "demo_s2tt",
            "task": "s2tt",
            "input": "speech",
            "output": "text",
            "spec_path": "/tmp/agent.yaml",
            "spec_sha256": "a" * 64,
        },
        "stages": [],
        "datasets": [
            {
                "dataset": DATASET,
                "source_root": "/tmp/src/mini_s2tt",
                "source_dataset_name": "mini_s2tt",
                "version_id": "unversioned",
                "task": "S2TT",
                "language": "zh",
                "translation_language": "en",
                "sample_jsonl": "/tmp/src/mini_s2tt/sample.jsonl",
                "ds_jsonl": "/tmp/src/mini_s2tt/ds.jsonl",
                "raw_dir": "/tmp/src/mini_s2tt",
                "num_samples": 2,
            }
        ],
        "metrics": ["bleu"],
        "runtime": {"product_dir": str(product_dir), "output_dir": None, "dataset_source_key": "default"},
    }


def write_bundle(product_dir: Path) -> None:
    (product_dir / "predictions").mkdir(parents=True, exist_ok=True)
    (product_dir / "references" / "sure_benchmark" / "jsonl").mkdir(parents=True, exist_ok=True)
    prediction_file = product_dir / "predictions" / f"{DATASET}.txt"
    prediction_file.write_text("utt0\thello\nutt1\tworld\n", encoding="utf-8")
    write_json(
        product_dir / "predictions" / "manifest.json",
        {
            "datasets": {
                DATASET: {
                    "prediction_file": f"predictions/{DATASET}.txt",
                    "sha256": hashlib.sha256(prediction_file.read_bytes()).hexdigest(),
                    "rows": 2,
                }
            }
        },
    )
    (product_dir / "protocol.yaml").write_text("schema: sure.agent_eval.inference_protocol.v1\n", encoding="utf-8")
    write_json(
        product_dir / "prediction_generation_status.json",
        {
            "schema": "sure.eval.prediction_generation_status.v2",
            "datasets": [
                {"dataset": DATASET, "status": "completed", "num_expected_samples": 2, "num_generated_samples": 2}
            ],
        },
    )
    (product_dir / "references" / "sure_benchmark" / "jsonl" / f"{DATASET}.jsonl").write_text(
        '{"key": "utt0"}\n{"key": "utt1"}\n', encoding="utf-8"
    )


def write_scoring_evidence(batch_dir: Path, *, score: float = 42.0) -> None:
    write_json(batch_dir / "validation_payload.json", {"is_valid": True, "results": [{"dataset": DATASET}]})
    write_json(
        batch_dir / "evaluation_payload.json",
        {
            "schema": "sure.eval.payload.v2",
            "results": [
                {
                    "dataset": DATASET,
                    "metric": "bleu",
                    "pipeline_id": "s2tt.zh.bleu.sacrebleu_zh_v1",
                    "result": {"score": score},
                }
            ],
        },
    )


def make_execution(product_dir: Path, **overrides) -> dict:
    result = {
        "schema": "sure.agent_eval.execution_result.v1",
        "job_status": "succeeded",
        "exit_code": 0,
        "failed_stage": None,
        "failed_dataset": None,
        "error": None,
        "product_dir": str(product_dir),
        "agent": {"name": "demo_s2tt", "task": "s2tt"},
        "datasets": [{"dataset": DATASET, "expected": 2, "generated": 2}],
        "created_at": "2026-09-10T00:00:00Z",
    }
    result.update(overrides)
    return result


def make_eval_report(product_dir: Path, batch_id: str, **overrides) -> dict:
    report = {
        "schema": "sure.agent_eval.eval_run_report.v1",
        "run_id": "run_test",
        "status": "success",
        "error_code": None,
        "agent": {"name": "demo_s2tt", "spec_sha256": "a" * 64},
        "evaluation_only": True,
        "batch_id": batch_id,
        "product_dir": str(product_dir),
        "evaluation_runs_dir": str(product_dir / "evaluation_runs" / batch_id),
        "datasets": [
            {
                "dataset": DATASET,
                "task": "S2TT",
                "language": "zh",
                "num_samples": 2,
                "metrics": [{"metric": "bleu", "pipeline_id": "s2tt.zh.bleu.sacrebleu_zh_v1", "score": 42.0}],
            }
        ],
        "engine": None,
        "created_at": "2026-09-10T00:00:00Z",
    }
    report.update(overrides)
    return report


def make_run_report(product_dir: Path, **overrides) -> dict:
    report = {
        "run_id": "run_test",
        "timestamp": "2026-09-10T00:00:00Z",
        "task_type": "evaluate_agent",
        "goal": "agent evaluation",
        "agent_name": "demo_s2tt",
        "selected_datasets": [DATASET],
        "executed_steps": ["run_agent", "evaluate"],
        "status": "success",
        "report_persisted": True,
        "execution_path_actual": "local",
        "run_dir": str(product_dir),
    }
    report.update(overrides)
    return report


class GateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.run_dir = self.tmp / "run"
        self.artifacts = self.run_dir / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.product_dir = self.tmp / "product"
        write_bundle(self.product_dir)
        write_json(self.artifacts / "agent_spec_resolved.json", make_spec(self.product_dir))

    def tearDown(self) -> None:
        self._tmp.cleanup()


class CheckAgentExecutionTests(GateTestCase):
    def check(self, result: dict) -> list[str]:
        produces = self.artifacts / "execution_result.json"
        write_json(produces, result)
        return check_agent_execution.gate_errors(self.run_dir, produces)

    def test_accepts_a_succeeded_run(self) -> None:
        self.assertEqual(self.check(make_execution(self.product_dir)), [])

    def test_accepts_a_documented_failure(self) -> None:
        errors = self.check(
            make_execution(
                self.product_dir,
                job_status="failed",
                exit_code=1,
                failed_stage="translate",
                failed_dataset=DATASET,
                error="sample utt0: boom",
                datasets=[],
            )
        )
        self.assertEqual(errors, [])

    def test_rejects_an_undocumented_failure(self) -> None:
        errors = self.check(make_execution(self.product_dir, job_status="failed", datasets=[]))
        self.assertTrue(any("error" in error for error in errors))
        self.assertTrue(any("failed_stage" in error for error in errors))

    def test_rejects_a_failure_whose_reason_is_not_a_string(self) -> None:
        errors = self.check(
            make_execution(
                self.product_dir,
                job_status="failed",
                exit_code=1,
                failed_stage={"stage": "translate"},
                failed_dataset=DATASET,
                error={"code": 7},
                datasets=[],
            )
        )
        self.assertTrue(any("error as a non-empty string" in error for error in errors))
        self.assertTrue(any("failed_stage as a non-empty string" in error for error in errors))

    def test_rejects_a_dataset_row_that_declares_no_counts(self) -> None:
        # Without the counts every prediction-side check compares 0 against 0 and
        # the is_file() guard skips the digest, so the whole row went unchecked.
        (self.product_dir / "predictions" / f"{DATASET}.txt").unlink()
        errors = self.check(make_execution(self.product_dir, datasets=[{"dataset": DATASET}]))
        self.assertTrue(any("expected and generated" in error for error in errors))

    def test_rejects_a_product_dir_that_differs_from_the_plan(self) -> None:
        errors = self.check(make_execution(self.tmp / "elsewhere"))
        self.assertTrue(any("product_dir" in error for error in errors))

    def test_rejects_short_prediction_files(self) -> None:
        (self.product_dir / "predictions" / f"{DATASET}.txt").write_text("utt0\thello\n", encoding="utf-8")
        errors = self.check(make_execution(self.product_dir))
        self.assertTrue(any("non-empty rows" in error for error in errors))

    def test_rejects_a_manifest_sha256_that_the_prediction_file_does_not_back(self) -> None:
        write_json(
            self.product_dir / "predictions" / "manifest.json",
            {"datasets": {DATASET: {"prediction_file": f"predictions/{DATASET}.txt", "sha256": "0" * 64, "rows": 2}}},
        )
        errors = self.check(make_execution(self.product_dir))
        self.assertTrue(any("sha256" in error for error in errors))

    def test_rejects_missing_protocol(self) -> None:
        (self.product_dir / "protocol.yaml").unlink()
        errors = self.check(make_execution(self.product_dir))
        self.assertTrue(any("protocol.yaml" in error for error in errors))


class CheckAgentEvalReportTests(GateTestCase):
    BATCH = "agent_eval_" + "1" * 24

    def setUp(self) -> None:
        super().setUp()
        self.batch_dir = self.product_dir / "evaluation_runs" / self.BATCH
        self.batch_dir.mkdir(parents=True)
        write_scoring_evidence(self.batch_dir)

    def check(self, report: dict) -> list[str]:
        produces = self.artifacts / "eval_run_report.json"
        write_json(produces, report)
        return check_agent_eval_report.gate_errors(self.run_dir, produces)

    def test_accepts_a_successful_report(self) -> None:
        self.assertEqual(self.check(make_eval_report(self.product_dir, self.BATCH)), [])

    def test_accepts_a_failed_report_with_error_code(self) -> None:
        errors = self.check(
            make_eval_report(
                self.product_dir,
                self.BATCH,
                status="failed",
                error_code="EVALUATION_FAILED",
                datasets=[],
            )
        )
        self.assertEqual(errors, [])

    def test_rejects_a_failed_report_without_error_code(self) -> None:
        errors = self.check(make_eval_report(self.product_dir, self.BATCH, status="failed", datasets=[]))
        self.assertTrue(any("error_code" in error for error in errors))

    def test_rejects_a_missing_dataset(self) -> None:
        errors = self.check(make_eval_report(self.product_dir, self.BATCH, datasets=[]))
        self.assertTrue(any(DATASET in error for error in errors))

    def test_rejects_a_dataset_without_numeric_scores(self) -> None:
        report = make_eval_report(self.product_dir, self.BATCH)
        report["datasets"][0]["metrics"] = [{"metric": "bleu", "pipeline_id": "p", "score": None}]
        errors = self.check(report)
        self.assertTrue(any("numeric" in error for error in errors))

    def test_rejects_a_non_numeric_metric_beside_a_valid_one(self) -> None:
        report = make_eval_report(self.product_dir, self.BATCH)
        report["datasets"][0]["metrics"].append({"metric": "junk", "pipeline_id": "p", "score": {"value": 1}})
        errors = self.check(report)
        self.assertTrue(any("numeric or null score" in error for error in errors))

    def test_rejects_a_missing_batch_directory(self) -> None:
        report = make_eval_report(self.product_dir, self.BATCH)
        report["evaluation_runs_dir"] = str(self.product_dir / "evaluation_runs" / ("agent_eval_" + "2" * 24))
        errors = self.check(report)
        self.assertTrue(errors)

    def test_rejects_an_agent_identity_drift(self) -> None:
        report = make_eval_report(self.product_dir, self.BATCH)
        report["agent"]["spec_sha256"] = "b" * 64
        errors = self.check(report)
        self.assertTrue(any("spec_sha256" in error for error in errors))

    def test_rejects_a_success_report_without_scoring_evidence(self) -> None:
        (self.batch_dir / "validation_payload.json").unlink()
        (self.batch_dir / "evaluation_payload.json").unlink()
        errors = self.check(make_eval_report(self.product_dir, self.BATCH))
        self.assertTrue(any("validation_payload.json" in error for error in errors))
        self.assertTrue(any("evaluation_payload.json" in error for error in errors))

    def test_rejects_a_validation_payload_that_did_not_pass(self) -> None:
        write_json(self.batch_dir / "validation_payload.json", {"is_valid": False, "results": []})
        errors = self.check(make_eval_report(self.product_dir, self.BATCH))
        self.assertTrue(any("is_valid" in error for error in errors))

    def test_rejects_a_score_the_evaluation_payload_does_not_back(self) -> None:
        report = make_eval_report(self.product_dir, self.BATCH)
        report["datasets"][0]["metrics"][0]["score"] = 99.0
        errors = self.check(report)
        self.assertTrue(any("evaluation payload" in error for error in errors))

    def test_rejects_a_metric_absent_from_the_evaluation_payload(self) -> None:
        report = make_eval_report(self.product_dir, self.BATCH)
        report["datasets"][0]["metrics"][0]["metric"] = "wer"
        errors = self.check(report)
        self.assertTrue(any("wer" in error for error in errors))


class CheckAgentRunReportTests(GateTestCase):
    BATCH = "agent_eval_" + "1" * 24

    def setUp(self) -> None:
        super().setUp()
        (self.product_dir / "evaluation_runs" / self.BATCH).mkdir(parents=True)

    def check(self, report: dict, *, with_eval_report: bool = True) -> list[str]:
        produces = self.artifacts / "main_agent_run_report.json"
        write_json(produces, report)
        if with_eval_report:
            write_json(
                self.artifacts / "eval_run_report.json",
                make_eval_report(self.product_dir, self.BATCH),
            )
        return check_agent_run_report.gate_errors(self.run_dir, produces)

    def test_accepts_a_completed_run(self) -> None:
        self.assertEqual(self.check(make_run_report(self.product_dir)), [])

    def test_rejects_an_unpersisted_report(self) -> None:
        errors = self.check(make_run_report(self.product_dir, report_persisted=False))
        self.assertTrue(any("report_persisted" in error for error in errors))

    def test_rejects_a_run_dir_that_is_not_the_product_dir(self) -> None:
        errors = self.check(make_run_report(self.product_dir, run_dir=str(self.tmp)))
        self.assertTrue(any("run_dir" in error for error in errors))

    def test_rejects_success_without_a_successful_eval_report(self) -> None:
        errors = self.check(make_run_report(self.product_dir), with_eval_report=False)
        self.assertTrue(any("eval_run_report" in error for error in errors))

    def test_accepts_a_failed_run_with_evidence_and_next_action(self) -> None:
        write_json(self.artifacts / "execution_result.json", make_execution(self.product_dir, job_status="failed"))
        errors = self.check(
            make_run_report(self.product_dir, status="failed", next_action="inspect the stage logs"),
            with_eval_report=False,
        )
        self.assertEqual(errors, [])

    def test_rejects_a_failed_run_without_next_action(self) -> None:
        errors = self.check(make_run_report(self.product_dir, status="failed"), with_eval_report=False)
        self.assertTrue(any("next_action" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
