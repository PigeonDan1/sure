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

    def test_a_node_that_cannot_run_containers_still_blocks_a_local_docker_run(self) -> None:
        # Here the probe's docker is the execution environment. Waving this
        # through only moves the same failure a few minutes later, with no
        # gate left to catch it.
        with patch.object(checks, "_live_runtime_probe", return_value=_host_cannot_probe()):
            result = checks.check_inference_runtime(self.write_surface())

        self.assertFalse(result["passed"])


def _host_cannot_probe() -> dict:
    return {
        "passed": False,
        "probe_ran": False,
        "failure_class": "PROBE_HOST_CANNOT_RUN_CONTAINERS",
        "exit_code": None,
        "evidence": "PROBE_HOST_CANNOT_RUN_CONTAINERS: docker is not on PATH",
    }


class EntrypointProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.artifacts = Path(self.temp.name) / "artifacts"
        self.artifacts.mkdir()
        self.entrypoint_sha = checks._sha256_file(checks.INFER_ENTRYPOINT)

    def write_surface(self, *, provenance: dict | None = None, **overrides: object) -> Path:
        surface: dict = {
            "entrypoint_path": str(checks.INFER_ENTRYPOINT),
            "source_provenance": {
                "template_file": str(checks.INFER_ENTRYPOINT),
                "template_sha256": self.entrypoint_sha,
                "isolation_compliance": {"eval_runs_referenced": False, "prior_run_scripts_copied": False},
            },
        }
        if provenance is not None:
            surface["source_provenance"] = provenance
        surface.update(overrides)
        path = self.artifacts / "execution_surface.json"
        path.write_text(json.dumps(surface), encoding="utf-8")
        return path

    def test_the_bundled_entrypoint_passes(self) -> None:
        result = checks.check_entrypoint_provenance(self.write_surface())
        self.assertTrue(result["passed"], result["evidence"])
        self.assertEqual(result["entrypoint_sha256"], self.entrypoint_sha)

    def test_another_script_is_rejected(self) -> None:
        other = Path(self.temp.name) / "run_evaluation.sh"
        other.write_text("#!/bin/bash\n", encoding="utf-8")
        result = checks.check_entrypoint_provenance(
            self.write_surface(
                entrypoint_path=str(other),
                provenance={
                    "template_file": str(other),
                    "template_sha256": checks._sha256_file(other),
                    "isolation_compliance": {"eval_runs_referenced": False, "prior_run_scripts_copied": False},
                },
            )
        )
        self.assertFalse(result["passed"])
        self.assertIn("bundled entrypoint", result["evidence"])

    def test_a_stale_digest_is_rejected(self) -> None:
        provenance = {
            "template_file": str(checks.INFER_ENTRYPOINT),
            "template_sha256": "0" * 64,
            "isolation_compliance": {"eval_runs_referenced": False, "prior_run_scripts_copied": False},
        }
        result = checks.check_entrypoint_provenance(self.write_surface(provenance=provenance))
        self.assertFalse(result["passed"])
        self.assertIn("stale", result["evidence"])

    def test_a_missing_template_file_is_rejected(self) -> None:
        result = checks.check_entrypoint_provenance(self.write_surface(provenance={"template_sha256": self.entrypoint_sha}))
        self.assertFalse(result["passed"])
        self.assertIn("template_file is empty", result["evidence"])

    def test_prior_run_leakage_is_rejected(self) -> None:
        provenance = {
            "template_file": str(checks.INFER_ENTRYPOINT),
            "template_sha256": self.entrypoint_sha,
            "isolation_compliance": {"eval_runs_referenced": True, "prior_run_scripts_copied": False},
        }
        result = checks.check_entrypoint_provenance(self.write_surface(provenance=provenance))
        self.assertFalse(result["passed"])
        self.assertIn("eval_runs_referenced=true", result["evidence"])

    def test_a_missing_surface_is_rejected(self) -> None:
        result = checks.check_entrypoint_provenance(self.artifacts / "execution_surface.json")
        self.assertFalse(result["passed"])
        self.assertIn("not found", result["evidence"])


class ExpectedBindingSummaryTests(unittest.TestCase):
    def test_a_v2_container_binding_carries_its_image(self) -> None:
        approved = {
            "schema": "sure.eval.deployment_binding.v2",
            "runtime_kind": "container",
            "target_image_ref": IMAGE_REF,
            "policy": {"execution_mode": "container_only", "model_integrity": "image_digest"},
            "evidence": {"bundle_identity_sha256": "b" * 64},
        }
        self.assertEqual(
            checks.expected_binding_summary(approved),
            {
                "schema": "sure.eval.deployment_binding.v2",
                "runtime_kind": "container",
                "target_image_ref": IMAGE_REF,
                "bundle_identity_sha256": "b" * 64,
                "execution_mode": "container_only",
                "model_mount_read_only": True,
                "model_integrity": "image_digest",
                "result_mount_writable": True,
            },
        )

    def test_a_v2_python_binding_has_no_image_and_no_read_only_mount(self) -> None:
        approved = {
            "schema": "sure.eval.deployment_binding.v2",
            "runtime_kind": "python",
            "policy": {"execution_mode": "python", "model_integrity": "verify_before_after"},
            "evidence": {"bundle_identity_sha256": "b" * 64},
        }
        summary = checks.expected_binding_summary(approved)
        self.assertNotIn("target_image_ref", summary)
        self.assertFalse(summary["model_mount_read_only"])
        self.assertEqual(summary["model_integrity"], "verify_before_after")
        self.assertEqual(summary["execution_mode"], "python")

    def test_a_v1_binding_keeps_the_legacy_shape(self) -> None:
        approved = {
            "schema": "sure.eval.deployment_binding.v1",
            "target_image_ref": IMAGE_REF,
            "evidence": {"bundle_identity_sha256": "b" * 64},
        }
        self.assertEqual(
            checks.expected_binding_summary(approved),
            {
                "schema": "sure.eval.deployment_binding.v1",
                "target_image_ref": IMAGE_REF,
                "bundle_identity_sha256": "b" * 64,
                "execution_mode": "container_only",
                "model_mount_read_only": True,
                "result_mount_writable": True,
            },
        )

    def test_binding_mismatches_name_every_drifted_field(self) -> None:
        expected = {"schema": "sure.eval.deployment_binding.v2", "runtime_kind": "container"}
        self.assertEqual(checks.binding_mismatches(None, expected), ["execution surface must declare deployment_binding"])
        self.assertEqual(
            checks.binding_mismatches({"schema": "sure.eval.deployment_binding.v2", "runtime_kind": "python"}, expected),
            ["deployment_binding.runtime_kind must equal approved value 'container'"],
        )
        self.assertEqual(checks.binding_mismatches({**expected, "extra": 1}, expected), [])


def _probe_fixture() -> tuple[dict, dict]:
    harness = {
        "runtime_id": "sure-harness-test",
        "lock_sha256": "c" * 64,
        "python_executable": "/opt/sure-harness/bin/python",
        "manifest_path": "/opt/sure-harness/runtime-manifest.json",
        "runtime_root": "/opt/sure-harness",
    }
    binding = {
        "target_image_ref": IMAGE_REF,
        "container": {"python_executable": "python", "harness_runtime": harness},
    }
    return binding, harness


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

    def test_a_missing_container_runtime_is_a_limit_of_this_node(self) -> None:
        # Nothing was learned about the image here: the node cannot start a
        # container at all. Reporting that as an image problem sends whoever
        # reads the report looking in the wrong place.
        def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            raise FileNotFoundError(2, "No such file or directory", "docker")

        binding, harness = _probe_fixture()

        result = LIVE_RUNTIME_PROBE(binding, harness, run=run)

        self.assertFalse(result["passed"])
        self.assertFalse(result["probe_ran"])
        self.assertEqual(result["failure_class"], "PROBE_HOST_CANNOT_RUN_CONTAINERS")

    def test_docker_refusing_to_start_the_container_is_a_limit_of_this_node(self) -> None:
        # 125 is docker's own code for "I could not run this": no daemon, an
        # image that will not pull, a flag it does not know. The image never
        # got the chance to answer.
        def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=125, stdout="", stderr="Cannot connect to the Docker daemon")

        binding, harness = _probe_fixture()

        result = LIVE_RUNTIME_PROBE(binding, harness, run=run)

        self.assertFalse(result["probe_ran"])
        self.assertEqual(result["failure_class"], "PROBE_HOST_CANNOT_RUN_CONTAINERS")

    def test_a_contract_failure_inside_the_container_is_not_a_limit_of_this_node(self) -> None:
        # 41 can only come from the probe script, which only runs once the
        # container is up. The image answered, and the answer was no.
        def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=41, stdout="", stderr="ModuleNotFoundError: structlog")

        binding, harness = _probe_fixture()

        result = LIVE_RUNTIME_PROBE(binding, harness, run=run)

        self.assertTrue(result["probe_ran"])
        self.assertEqual(result["failure_class"], "HARNESS_RUNTIME_NOT_READY")


if __name__ == "__main__":
    unittest.main()
