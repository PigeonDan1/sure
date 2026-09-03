#!/usr/bin/env python3
"""Tests for run_infer.py, the host-side launcher of infer_entrypoint.py."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_execution_surface_compliance as compliance
import run_infer

IMAGE_REF = "registry.example.com/sure/demo@sha256:" + "a" * 64
SOURCE_ENTRY = "/srv/sure/datasets/group/store/ds_pool/demo_ds@v1.0.2"
PROBE_PASSED = {"passed": True, "failure_class": None, "evidence": "test runtime probe passed"}


class RunInferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run_dir = self.root / "run"
        self.artifacts = self.run_dir / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.product_dir = self.root / "product"
        self.product_dir.mkdir()
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        harness_root = self.root / "harness"
        self.harness_python = harness_root / "bin" / "python"
        self.harness_python.parent.mkdir(parents=True)
        self.harness_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.harness_python.chmod(0o755)
        manifest = harness_root / "runtime-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "sure.harness.runtime.manifest.v1",
                    "runtime_id": "sure-harness-test",
                    "lock_sha256": "c" * 64,
                    "python_version": "3.11.5",
                    "python_abi": "cp311",
                    "harness_version": "test",
                }
            ),
            encoding="utf-8",
        )
        self.harness_runtime = {
            "schema": "sure.harness.runtime.binding.v1",
            "runtime_id": "sure-harness-test",
            "python_executable": str(self.harness_python),
            "lock_sha256": "c" * 64,
            "manifest_path": str(manifest),
            "runtime_root": str(harness_root),
        }
        self.container_binding = {
            "schema": "sure.eval.deployment_binding.v2",
            "runtime_kind": "container",
            "model_dir": str(self.model_dir),
            "target_image": "registry.example.com/sure/demo:v1",
            "target_image_digest": "sha256:" + "a" * 64,
            "target_image_ref": IMAGE_REF,
            "container": {"tool_names": ["transcribe_audio"], "python_executable": "python"},
            "policy": {"execution_mode": "container_only", "model_integrity": "image_digest", "host_python_fallback": False},
            "evidence": {"bundle_identity_sha256": "b" * 64},
        }
        probe = patch.object(compliance, "_live_runtime_probe", return_value=PROBE_PASSED)
        probe.start()
        self.addCleanup(probe.stop)

    def write_inputs(self, binding: dict, *, path_planned: str = "local_docker", requested_name: str = SOURCE_ENTRY) -> None:
        eval_input = {
            "user_input": {
                "model": "demo",
                "datasets": [SOURCE_ENTRY],
                "metrics": ["cer"],
                "max_samples": 0,
                "protocol": "standard_system",
                "device": "cpu",
            },
            "model": {"model_dir": str(self.model_dir), "deployment_binding": binding},
            "datasets": [{"name": "demo_ds__v1.0.2", "requested_name": requested_name, "language": "zh", "task": "ASR"}],
            "runtime": {
                "run_id": "run-1",
                "run_dir": str(self.product_dir),
                "protocol_id": "standard_system",
                "max_samples": 0,
                "device": {"request": "cpu", "resolved": "cpu"},
                "execution": {
                    "requested": "local",
                    "planned": "local",
                    "path_requested": "auto",
                    "path_planned": path_planned,
                    "fallback_allowed": False,
                    "reason": "user_requested_local",
                },
                "execution_path": path_planned,
                "model_runtime": "python" if path_planned == "local_python" else "container",
                "harness_runtime": self.harness_runtime,
            },
        }
        (self.artifacts / "eval_input_resolved.json").write_text(json.dumps(eval_input), encoding="utf-8")
        (self.artifacts / "dataset_decision.json").write_text(
            json.dumps({"selection_basis": [], "selected_datasets": ["demo_ds__v1.0.2"], "skipped_datasets": []}),
            encoding="utf-8",
        )

    def write_product(self, rows: int = 3) -> None:
        predictions = self.product_dir / "predictions"
        predictions.mkdir(exist_ok=True)
        (predictions / "demo_ds__v1.0.2.txt").write_text("".join(f"k{i}\tp{i}\n" for i in range(rows)), encoding="utf-8")
        (self.product_dir / "prediction_generation_status.json").write_text(
            json.dumps(
                {
                    "schema": "sure.eval.prediction_generation_status.v2",
                    "datasets": [
                        {"dataset": "demo_ds__v1.0.2", "status": "completed", "num_expected_samples": rows, "num_generated_samples": rows}
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.product_dir / "validation_payload.json").write_text(
            json.dumps(
                {
                    "is_valid": True,
                    "results": [
                        {
                            "dataset": "demo_ds__v1.0.2",
                            "expected_samples": rows,
                            "provided_predictions": rows,
                            "empty_prediction_keys": [],
                            "contract_violation_keys": [],
                            "is_valid": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def run_container(self, command: list[str]) -> tuple[int, dict, dict, dict]:
        with (
            patch.object(sys, "argv", ["run_infer.py", "--run-dir", str(self.run_dir)]),
            patch.object(run_infer, "build_local_container_command", return_value=(command, {"image_ref": IMAGE_REF})),
        ):
            return_code = run_infer.main()
        result = json.loads((self.artifacts / "execution_result.json").read_text(encoding="utf-8"))
        submit = json.loads((self.artifacts / "submit_result.json").read_text(encoding="utf-8"))
        surface = json.loads((self.artifacts / "execution_surface.json").read_text(encoding="utf-8"))
        return return_code, result, submit, surface

    def test_command_failure_propagates_to_execution_result(self) -> None:
        self.write_inputs(self.container_binding)
        return_code, result, submit, _ = self.run_container([sys.executable, "-c", "import sys; sys.exit(23)"])
        self.assertEqual(return_code, 23)
        self.assertEqual(result["exit_code"], 23)
        self.assertEqual(result["job_status"], "failed")
        self.assertEqual(result["failed_stage"], "")
        # Transitional artifact for the submit_vc_run unit: nothing but the two routing facts.
        self.assertEqual(submit, {"execution_path": "local_docker", "runtime_kind": "container"})

    def test_site_docker_wrapper_text_propagates_inner_exit(self) -> None:
        self.write_inputs(self.container_binding)
        return_code, result, _, _ = self.run_container(
            [sys.executable, "-c", "print('Error: exit status 37')"]
        )
        self.assertEqual(return_code, 37)
        self.assertEqual(result["exit_code"], 37)
        self.assertEqual(result["job_status"], "failed")

    def test_failed_stage_is_taken_from_the_entrypoint_marker(self) -> None:
        self.write_inputs(self.container_binding)
        _, result, _, _ = self.run_container(
            [sys.executable, "-c", "print('ERROR'); print('INFER_STAGE_FAILED smoke'); raise SystemExit(1)"]
        )
        self.assertEqual(result["failed_stage"], "smoke")
        self.assertEqual(result["job_status"], "failed")

    def test_a_clean_run_records_the_product_tree_and_input_digest(self) -> None:
        self.write_inputs(self.container_binding)
        self.write_product(rows=3)
        return_code, result, _, surface = self.run_container([sys.executable, "-c", "pass"])
        self.assertEqual(return_code, 0)
        self.assertEqual(result["job_status"], "succeeded")
        self.assertEqual(result["product_dir"], str(self.product_dir))
        self.assertEqual(result["datasets"], [{"dataset": "demo_ds__v1.0.2", "expected": 3, "generated": 3, "valid": 3}])
        self.assertRegex(result["input_digest"], r"^[a-f0-9]{64}$")
        self.assertEqual(result["execution_path"], "local_docker")
        self.assertEqual(surface["execution"]["path_planned"], "local_docker")

    def test_surface_tool_name_comes_from_the_approved_binding(self) -> None:
        self.write_inputs(self.container_binding)
        _, _, _, surface = self.run_container([sys.executable, "-c", "pass"])
        self.assertEqual(surface["env"]["TOOL_NAME"], "transcribe_audio")
        self.assertEqual(surface["resolved_inputs"]["tool_name"], "transcribe_audio")
        self.assertEqual(surface["env"]["DATASETS"], SOURCE_ENTRY)
        self.assertEqual(surface["env"]["SURE_EVAL_INPUT_RESOLVED"], str(self.artifacts / "eval_input_resolved.json"))
        self.assertEqual(surface["entrypoint_path"], str(run_infer.ENTRYPOINT))
        self.assertEqual(surface["source_provenance"]["template_file"], str(run_infer.ENTRYPOINT))
        self.assertEqual(surface["source_provenance"]["template_sha256"], run_infer._sha256_file(run_infer.ENTRYPOINT))

    def test_surface_matches_the_v2_schema(self) -> None:
        self.write_inputs(self.container_binding)
        _, _, _, surface = self.run_container([sys.executable, "-c", "pass"])
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "execution_surface_v2.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(surface) - set(schema["properties"]), set(), "surface carries keys the schema forbids")
        for key in schema["required"]:
            self.assertIn(key, surface)
        binding_schema = schema["properties"]["deployment_binding"]
        self.assertEqual(set(surface["deployment_binding"]) - set(binding_schema["properties"]), set())
        for key in binding_schema["required"]:
            self.assertIn(key, surface["deployment_binding"])
        provenance_schema = schema["properties"]["source_provenance"]
        self.assertEqual(set(surface["source_provenance"]) - set(provenance_schema["properties"]), set())
        self.assertTrue(all(isinstance(value, str) for value in surface["env"].values()))

    def test_v1_binding_is_refused(self) -> None:
        legacy = {
            "schema": "sure.eval.deployment_binding.v1",
            "target_image_ref": IMAGE_REF,
            "container": {"tool_names": ["transcribe_audio"]},
            "policy": {"execution_mode": "container_only", "host_python_fallback": False},
            "evidence": {"bundle_identity_sha256": "b" * 64},
        }
        self.write_inputs(legacy)
        with patch.object(sys, "argv", ["run_infer.py", "--run-dir", str(self.run_dir)]):
            with self.assertRaisesRegex(RuntimeError, "schema v1 is no longer supported; re-run /sure_approve"):
                run_infer.main()
        self.assertFalse((self.artifacts / "execution_surface.json").exists())

    def test_a_dropped_version_suffix_is_refused_before_launch(self) -> None:
        self.write_inputs(self.container_binding, requested_name=SOURCE_ENTRY.split("@", 1)[0])
        with patch.object(sys, "argv", ["run_infer.py", "--run-dir", str(self.run_dir)]):
            with self.assertRaisesRegex(RuntimeError, "@version"):
                run_infer.main()

    def test_a_tampered_surface_would_fail_compliance_before_launch(self) -> None:
        self.write_inputs(self.container_binding)
        launched: list[list[str]] = []

        def fake_compliance(surface_path: Path) -> None:
            raise RuntimeError("EXECUTION_SURFACE_ISOLATION red line: entrypoint_provenance: stale")

        with (
            patch.object(sys, "argv", ["run_infer.py", "--run-dir", str(self.run_dir)]),
            patch.object(run_infer, "_run_compliance", fake_compliance),
            patch.object(run_infer, "build_local_container_command", side_effect=lambda **kw: launched.append(kw) or ([], {})),
        ):
            with self.assertRaisesRegex(RuntimeError, "EXECUTION_SURFACE_ISOLATION"):
                run_infer.main()
        self.assertEqual(launched, [])
        self.assertFalse((self.artifacts / "execution_result.json").exists())

    def test_local_python_route_never_builds_a_container_command(self) -> None:
        python_binding = {
            "schema": "sure.eval.deployment_binding.v2",
            "runtime_kind": "python",
            "model_dir": str(self.model_dir),
            "python": {
                "runtime_id": "sure-model-python-v1-" + "a" * 24,
                "python_executable": sys.executable,
                "working_dir": str(self.model_dir),
                "tool_names": ["transcribe_audio"],
            },
            "policy": {"execution_mode": "python", "model_integrity": "verify_before_after", "host_python_fallback": False},
            "evidence": {"bundle_identity_sha256": "b" * 64, "model_core_sha256": {}},
        }
        self.write_inputs(python_binding, path_planned="local_python")
        launch = {"runtime_kind": "python", "model_runtime": python_binding["python"], "harness_runtime": {}}
        with (
            patch.object(sys, "argv", ["run_infer.py", "--run-dir", str(self.run_dir)]),
            patch.object(
                run_infer,
                "build_local_python_command",
                return_value=([sys.executable, "-c", "pass"], os.environ.copy(), launch),
            ),
            patch.object(run_infer, "build_local_container_command", side_effect=AssertionError("Docker route must not be used")),
            patch.object(run_infer, "verify_model_integrity", return_value={}),
        ):
            self.assertEqual(run_infer.main(), 0)
        submit = json.loads((self.artifacts / "submit_result.json").read_text(encoding="utf-8"))
        self.assertEqual(submit, {"execution_path": "local_python", "runtime_kind": "python"})
        surface = json.loads((self.artifacts / "execution_surface.json").read_text(encoding="utf-8"))
        self.assertEqual(surface["deployment_binding"]["runtime_kind"], "python")
        self.assertNotIn("target_image_ref", surface["deployment_binding"])


if __name__ == "__main__":
    unittest.main()
