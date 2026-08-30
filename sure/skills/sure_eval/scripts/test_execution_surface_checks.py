#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import check_execution_surface_compliance as checks


IMAGE_REF = "registry.example.com/sure/demo@sha256:" + "a" * 64
LIVE_RUNTIME_PROBE = checks._live_runtime_probe


class InferenceRuntimeCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.artifacts = Path(self.temp.name) / "artifacts"
        self.artifacts.mkdir()
        live = patch.object(
            checks,
            "_live_runtime_probe",
            return_value={
                "passed": True,
                "failure_class": None,
                "evidence": "test runtime probe passed",
            },
        )
        live.start()
        self.addCleanup(live.stop)
        self.approved = {
            "schema": "sure.eval.deployment_binding.v2",
            "runtime_kind": "container",
            "target_image_ref": IMAGE_REF,
            "container": {"tool_names": ["transcribe_audio"]},
            "policy": {
                "execution_mode": "container_only",
                "model_integrity": "image_digest",
                "host_python_fallback": False,
            },
            "evidence": {"bundle_identity_sha256": "b" * 64},
        }
        self.harness_root = Path(self.temp.name) / "harness"
        self.harness_python = self.harness_root / "bin" / "python"
        self.harness_python.parent.mkdir(parents=True)
        self.harness_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.harness_python.chmod(0o755)
        self.harness_manifest = self.harness_root / "runtime-manifest.json"
        self.harness_manifest.write_text(
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
        harness_runtime = {
            "schema": "sure.harness.runtime.binding.v1",
            "runtime_id": "sure-harness-test",
            "python_executable": str(self.harness_python),
            "lock_sha256": "c" * 64,
            "manifest_path": str(self.harness_manifest),
            "runtime_root": str(self.harness_root),
        }
        (self.artifacts / "eval_input_resolved.json").write_text(
            json.dumps(
                {
                    "model": {"deployment_binding": self.approved},
                    "runtime": {"harness_runtime": harness_runtime},
                }
            ),
            encoding="utf-8",
        )

    def write_surface(self, **overrides: object) -> Path:
        surface = {
            "execution": {"requested": "local", "path_planned": "local_docker"},
            "deployment_binding": {
                "schema": "sure.eval.deployment_binding.v2",
                "runtime_kind": "container",
                "target_image_ref": IMAGE_REF,
                "bundle_identity_sha256": "b" * 64,
                "execution_mode": "container_only",
                "model_integrity": "image_digest",
                "model_mount_read_only": True,
                "result_mount_writable": True,
            },
            "env": {"TOOL_NAME": "transcribe_audio"},
            "resolved_inputs": {"tool_name": "transcribe_audio"},
        }
        surface.update(overrides)
        path = self.artifacts / "execution_surface.json"
        path.write_text(json.dumps(surface), encoding="utf-8")
        return path

    def test_approved_local_docker_binding_passes(self) -> None:
        result = checks.check_inference_runtime(self.write_surface())
        self.assertTrue(result["passed"], result.get("evidence"))

    def test_vc_uses_the_same_binding(self) -> None:
        result = checks.check_inference_runtime(
            self.write_surface(execution={"requested": "vc", "path_planned": "vc_submit"})
        )
        self.assertTrue(result["passed"], result.get("evidence"))

    def test_legacy_v1_container_surface_still_passes(self) -> None:
        legacy_approved = {
            "schema": "sure.eval.deployment_binding.v1",
            "target_image_ref": IMAGE_REF,
            "container": {"tool_names": ["transcribe_audio"]},
            "policy": {
                "execution_mode": "container_only",
                "host_python_fallback": False,
            },
            "evidence": {"bundle_identity_sha256": "b" * 64},
        }
        eval_input_path = self.artifacts / "eval_input_resolved.json"
        eval_input = json.loads(eval_input_path.read_text(encoding="utf-8"))
        eval_input["model"]["deployment_binding"] = legacy_approved
        eval_input_path.write_text(json.dumps(eval_input), encoding="utf-8")
        legacy_declared = {
            "schema": "sure.eval.deployment_binding.v1",
            "target_image_ref": IMAGE_REF,
            "bundle_identity_sha256": "b" * 64,
            "execution_mode": "container_only",
            "model_mount_read_only": True,
            "result_mount_writable": True,
        }

        result = checks.check_inference_runtime(
            self.write_surface(deployment_binding=legacy_declared)
        )

        self.assertTrue(result["passed"], result.get("evidence"))

    def test_local_bash_is_rejected(self) -> None:
        result = checks.check_inference_runtime(
            self.write_surface(execution={"requested": "local", "path_planned": "local_bash"})
        )
        self.assertFalse(result["passed"])
        self.assertIn("local_docker", result["evidence"])

    def test_image_mismatch_is_rejected(self) -> None:
        declared = {
            "schema": "sure.eval.deployment_binding.v2",
            "runtime_kind": "container",
            "target_image_ref": "registry.example.com/sure/other@sha256:" + "c" * 64,
            "bundle_identity_sha256": "b" * 64,
            "execution_mode": "container_only",
            "model_integrity": "image_digest",
            "model_mount_read_only": True,
            "result_mount_writable": True,
        }
        result = checks.check_inference_runtime(self.write_surface(deployment_binding=declared))
        self.assertFalse(result["passed"])
        self.assertIn("target_image_ref", result["evidence"])

    def test_host_venv_is_rejected(self) -> None:
        result = checks.check_inference_runtime(
            self.write_surface(env={"TOOL_NAME": "transcribe_audio", "MODEL_PYTHON": "/nfs/model/.venv/bin/python"})
        )
        self.assertFalse(result["passed"])
        self.assertIn("host interpreter", result["evidence"])

    def test_tool_mismatch_is_rejected(self) -> None:
        result = checks.check_inference_runtime(
            self.write_surface(env={"TOOL_NAME": "other"}, resolved_inputs={"tool_name": "other"})
        )
        self.assertFalse(result["passed"])
        self.assertIn("approved deployment", result["evidence"])

    def test_harness_model_python_alias_is_rejected(self) -> None:
        value = str(self.harness_python)
        result = checks.check_inference_runtime(
            self.write_surface(
                env={
                    "TOOL_NAME": "transcribe_audio",
                    "HARNESS_PYTHON_BIN": value,
                    "MODEL_PYTHON": value,
                }
            )
        )
        self.assertFalse(result["passed"])
        self.assertIn("separate execution roles", result["evidence"])

    def test_declared_node_python_must_match_harness_runtime(self) -> None:
        result = checks.check_inference_runtime(
            self.write_surface(
                env={
                    "TOOL_NAME": "transcribe_audio",
                    "SURE_EVAL_NODE_LOCAL_PYTHON": "/usr/bin/python3.11",
                }
            )
        )
        self.assertFalse(result["passed"])
        self.assertIn("approved common Harness Runtime", result["evidence"])


class LiveRuntimeProbeTests(unittest.TestCase):
    def test_exact_image_probe_executes_node_override(self) -> None:
        commands: list[list[str]] = []

        def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        harness = {
            "runtime_id": "sure-harness-test",
            "lock_sha256": "c" * 64,
            "python_executable": "/opt/sure-harness/bin/python",
            "manifest_path": "/opt/sure-harness/runtime-manifest.json",
            "runtime_root": "/opt/sure-harness",
        }
        binding = {
            "target_image_ref": IMAGE_REF,
            "container": {
                "python_executable": "python",
                "harness_runtime": harness,
            },
        }

        result = LIVE_RUNTIME_PROBE(binding, harness, run=run)

        self.assertTrue(result["passed"])
        self.assertEqual(result["node_local_python"], harness["python_executable"])
        self.assertIn("SURE_EVAL_NODE_LOCAL_PYTHON", commands[0][-1])
        self.assertIn("subprocess.run", commands[0][-1])


if __name__ == "__main__":
    unittest.main()
