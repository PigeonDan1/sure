#!/usr/bin/env python3
"""Tests for the execute_inference gate over execution_result.json."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import check_execution_result as gate
import check_execution_surface_compliance as compliance

IMAGE_REF = "registry.example.com/sure/demo@sha256:" + "a" * 64
DATASET = "demo_ds__v1.0.2"


class CheckExecutionResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run_dir = self.root / "run"
        self.artifacts = self.run_dir / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.product_dir = self.root / "product"
        self.approved = {
            "schema": "sure.eval.deployment_binding.v2",
            "runtime_kind": "container",
            "target_image_ref": IMAGE_REF,
            "container": {"tool_names": ["transcribe_audio"]},
            "policy": {"execution_mode": "container_only", "model_integrity": "image_digest", "host_python_fallback": False},
            "evidence": {"bundle_identity_sha256": "b" * 64},
        }
        self.write_json(self.artifacts / "eval_input_resolved.json", {"model": {"deployment_binding": self.approved}})
        self.write_surface()
        self.write_product(rows=2)
        self.write_result()

    @staticmethod
    def write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def write_surface(self, **overrides: object) -> None:
        surface = {
            "execution": {"requested": "local", "path_planned": "local_docker"},
            "deployment_binding": compliance.expected_binding_summary(self.approved),
        }
        surface.update(overrides)
        self.write_json(self.artifacts / "execution_surface.json", surface)

    def write_product(self, rows: int, *, status: str = "completed") -> None:
        predictions = self.product_dir / "predictions"
        predictions.mkdir(parents=True, exist_ok=True)
        (predictions / f"{DATASET}.txt").write_text("".join(f"k{i}\tp{i}\n" for i in range(rows)), encoding="utf-8")
        self.write_json(
            self.product_dir / "prediction_generation_status.json",
            {"datasets": [{"dataset": DATASET, "status": status, "num_expected_samples": rows, "num_generated_samples": rows}]},
        )
        (self.product_dir / "protocol.yaml").write_text("schema: sure.eval.inference_protocol.v1\n", encoding="utf-8")
        references = self.product_dir / "references" / "sure_benchmark" / "jsonl"
        references.mkdir(parents=True, exist_ok=True)
        (references / f"{DATASET}.jsonl").write_text('{"key": "k0"}\n', encoding="utf-8")

    def write_result(self, **overrides: object) -> Path:
        result = {
            "job_status": "succeeded",
            "exit_code": 0,
            "execution_path": "local_docker",
            "runtime_kind": "container",
            "product_dir": str(self.product_dir),
            "failed_stage": "",
            "input_digest": "d" * 64,
            "datasets": [{"dataset": DATASET, "expected": 2, "generated": 2, "valid": 2}],
        }
        result.update(overrides)
        path = self.artifacts / "execution_result.json"
        self.write_json(path, result)
        return path

    def errors(self) -> list[str]:
        return gate.gate_errors(self.run_dir, self.artifacts / "execution_result.json")

    def test_a_clean_success_passes(self) -> None:
        self.assertEqual(self.errors(), [])

    def test_a_terminal_failure_is_a_valid_gate_outcome(self) -> None:
        self.write_result(job_status="failed", exit_code=3, failed_stage="generate", datasets=[])
        self.assertEqual(self.errors(), [])

    def test_a_running_job_is_refused(self) -> None:
        self.write_result(job_status="running", exit_code=None)
        self.assertTrue(any("still running" in error for error in self.errors()))

    def test_a_path_that_differs_from_the_plan_is_refused(self) -> None:
        self.write_result(execution_path="local_python")
        self.assertTrue(any("execution_path" in error and "local_docker" in error for error in self.errors()))

    def test_a_surface_binding_that_drifted_fails(self) -> None:
        drifted = compliance.expected_binding_summary(self.approved)
        drifted["bundle_identity_sha256"] = "e" * 64
        self.write_surface(deployment_binding=drifted)
        self.assertTrue(any("bundle_identity_sha256" in error for error in self.errors()))

    def test_a_missing_surface_is_refused(self) -> None:
        (self.artifacts / "execution_surface.json").unlink()
        self.assertTrue(any("run_infer.py" in error for error in self.errors()))

    def test_a_generated_count_that_disagrees_with_the_predictions_file_fails(self) -> None:
        self.write_result(datasets=[{"dataset": DATASET, "expected": 2, "generated": 5, "valid": 5}])
        self.assertTrue(any("non-empty rows" in error for error in self.errors()))

    def test_a_dataset_the_status_file_did_not_complete_fails(self) -> None:
        self.write_product(rows=2, status="running")
        self.assertTrue(any("not completed" in error for error in self.errors()))

    def test_a_missing_protocol_fails_a_success(self) -> None:
        (self.product_dir / "protocol.yaml").unlink()
        self.assertTrue(any("protocol.yaml" in error for error in self.errors()))

    def test_a_missing_reference_projection_fails_a_success(self) -> None:
        (self.product_dir / "references" / "sure_benchmark" / "jsonl" / f"{DATASET}.jsonl").unlink()
        self.assertTrue(any("references/sure_benchmark/jsonl" in error for error in self.errors()))

    def test_a_success_without_dataset_rows_fails(self) -> None:
        self.write_result(datasets=[])
        self.assertTrue(any("list its datasets" in error for error in self.errors()))

    def test_main_exit_codes(self) -> None:
        produces = str(self.artifacts / "execution_result.json")
        old_argv = sys.argv
        try:
            sys.argv = ["check_execution_result.py", "--run-dir", str(self.run_dir), "--produces", produces]
            self.assertEqual(gate.main(), 0)
            self.write_result(job_status="running", exit_code=None)
            self.assertEqual(gate.main(), 1)
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
