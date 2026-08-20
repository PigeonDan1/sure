#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from container_execution import build_local_container_command
from deployment_binding import DeploymentBindingError, load_deployment_binding
from check_run_report import _submitted_image_error
from run_vc_execution import _approved_memory_gb, _job_name, _normalize_job_name, _write_entrypoint


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DeploymentBindingTests(unittest.TestCase):
    def test_vc_job_name_is_bounded_and_stable(self) -> None:
        raw = "openai__" + "whisper-large-v3-turbo-" * 4
        first = _normalize_job_name(raw)
        second = _normalize_job_name(raw)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 60)
        self.assertRegex(first, r"^[a-z0-9.-]+-[0-9a-f]{8}$")

    def test_generated_and_explicit_vc_job_names_share_platform_limit(self) -> None:
        generated = _job_name(
            "openai__whisper-large-v3-turbo",
            {"runtime": {"run_id": "20260811-051124-0de179b0"}},
            {},
            {},
        )
        explicit = _job_name("model", {}, {}, {"job_name": "X" * 80})

        self.assertLessEqual(len(generated), 60)
        self.assertLessEqual(len(explicit), 60)
        self.assertEqual(explicit, _normalize_job_name("X" * 80))

    def test_submit_schema_declares_runtime_and_image_provenance(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "submit_result.schema.json"
        properties = json.loads(schema_path.read_text(encoding="utf-8"))["properties"]
        expected = {
            "image_digest",
            "image_identity_ref",
            "deployment_binding",
            "harness_runtime",
            "harness_runtime_mounted_from_repo",
            "model_runtime",
            "vc_submission",
        }

        self.assertTrue(expected.issubset(properties))

    def test_vc_submission_separates_platform_tag_from_digest_identity(self) -> None:
        approved = {
            "target_image": self.image,
            "target_image_digest": self.digest,
            "target_image_ref": self.image_ref,
        }
        submit = {
            "vc_image": self.image,
            "image_digest": self.digest,
            "image_identity_ref": self.image_ref,
            "vc_submit_command": f"vc submit -i {self.image} -j test",
            "vc_submission": {
                "image": self.image,
                "image_digest": self.digest,
                "image_identity_ref": self.image_ref,
            },
        }

        self.assertIsNone(_submitted_image_error("vc_submit", submit, approved))
        submit["vc_submission"]["image"] = self.image_ref
        self.assertIn("tag differs", _submitted_image_error("vc_submit", submit, approved) or "")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / "models" / "demo"
        self.artifacts = self.model / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.image = "registry.example.com/sure/demo:v1"
        self.digest = "sha256:" + "a" * 64
        self.image_ref = "registry.example.com/sure/demo@" + self.digest
        self.output = self.root / "results" / "run"
        self.output.mkdir(parents=True)
        self.control = self.root / "control"
        self.control.mkdir()
        self.entrypoint = self.control / "run_evaluation.sh"
        self.entrypoint.write_text("#!/bin/bash\n", encoding="utf-8")
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.harness_runtime = self.repo / "sure" / ".runtime" / "harness" / "demo"
        self.harness_python = self.harness_runtime / "bin" / "python"
        self.harness_python.parent.mkdir(parents=True)
        self.harness_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.harness_python.chmod(0o755)
        self.harness_manifest = self.harness_runtime / "runtime-manifest.json"
        write_json(
            self.harness_manifest,
            {
                "schema": "sure.harness.runtime.manifest.v1",
                "runtime_id": "sure-harness-test",
                "harness_version": "test",
                "python_version": "3.11.5",
                "python_abi": "cp311",
                "lock_sha256": "c" * 64,
                "materialization": "test",
                "materialization_version": 1,
            },
        )
        self._write_bundle()

    def _runtime_binding(self) -> dict:
        return {
            "schema": "sure.harness.runtime.binding.v1",
            "runtime_id": "sure-harness-test",
            "runtime_type": "harness_python",
            "python_executable": str(self.harness_python),
            "python_version": "3.11.5",
            "python_abi": "cp311",
            "harness_version": "test",
            "lock_sha256": "c" * 64,
            "manifest_path": str(self.harness_manifest),
            "runtime_root": str(self.harness_runtime),
        }

    def _write_bundle(self) -> None:
        write_json(
            self.artifacts / "runtime_inventory.json",
            {
                "schema": "sure.onboard.runtime_inventory.v2",
                "status": "ready",
                "model": {"name": "demo"},
                "container_runtime": {
                    "required": True,
                    "target_image": self.image,
                    "target_image_digest": self.digest,
                    "target_image_ref": self.image_ref,
                    "python_executable": "python",
                    "working_dir": "/workspace/model",
                    "server_command": ["python", "server.py"],
                    "tool_names": ["transcribe_audio"],
                    "gpu_required": True,
                    "mount_policy": {
                        "nfs_models_read_only": True,
                        "model_bundle": {"target": "/workspace/model", "read_only": True},
                        "result_workspace": {"target": "/sure-output", "read_only": False},
                    },
                },
                "policy": {
                    "eval_runtime": "container_only",
                    "host_python_fallback": False,
                    "image_override_allowed": False,
                    "nfs_models_mutable_by_eval": False,
                },
            },
        )
        write_json(
            self.artifacts / "package_gate.json",
            {
                "schema": "sure.onboard.package_gate.v2",
                "status": "passed",
                "package_profile": "docker-registry",
                "readiness": {
                    "local_ready": True,
                    "docker_ready": True,
                    "registry_ready": True,
                    "bundle_ready": True,
                },
                "docker": {
                    "target_image": self.image,
                    "target_image_digest": self.digest,
                    "target_image_ref": self.image_ref,
                },
            },
        )
        hashes = {
            "artifacts/runtime_inventory.json": sha256(self.artifacts / "runtime_inventory.json"),
            "artifacts/package_gate.json": sha256(self.artifacts / "package_gate.json"),
        }
        bundle_identity = hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        write_json(
            self.artifacts / "deployment_ready.json",
            {
                "schema": "sure.onboard.deployment_ready.v1",
                "status": "ready",
                "model_name": "demo",
                "package_profile": "docker-registry",
                "target_image": self.image,
                "target_image_digest": self.digest,
                "target_image_ref": self.image_ref,
                "bundle_identity_sha256": bundle_identity,
                "required_artifact_sha256": hashes,
                "execution_policy": {
                    "container_only": True,
                    "nfs_models_read_only": True,
                    "host_python_fallback": False,
                    "approved_image_override": False,
                },
            },
        )

    def test_loads_exact_digest_binding(self) -> None:
        binding = load_deployment_binding(self.model, "demo")
        self.assertEqual(binding["target_image_ref"], self.image_ref)
        self.assertTrue(binding["container"]["model_mount"]["read_only"])

    def test_hash_tamper_is_rejected(self) -> None:
        package = json.loads((self.artifacts / "package_gate.json").read_text())
        package["readiness"]["bundle_ready"] = False
        write_json(self.artifacts / "package_gate.json", package)
        with self.assertRaises(DeploymentBindingError):
            load_deployment_binding(self.model, "demo")

    def test_bundle_identity_tamper_is_rejected(self) -> None:
        marker = json.loads((self.artifacts / "deployment_ready.json").read_text())
        marker["bundle_identity_sha256"] = "b" * 64
        write_json(self.artifacts / "deployment_ready.json", marker)
        with self.assertRaisesRegex(DeploymentBindingError, "bundle identity"):
            load_deployment_binding(self.model, "demo")

    def test_local_command_uses_digest_and_read_only_model(self) -> None:
        binding = load_deployment_binding(self.model, "demo")
        command, provenance = build_local_container_command(
            surface={"env": {"TOOL_NAME": "transcribe_audio"}},
            eval_input={
                "model": {"deployment_binding": binding},
                "runtime": {
                    "run_dir": str(self.output),
                    "harness_runtime": self._runtime_binding(),
                },
                "datasets": [],
            },
            control_run_dir=self.control,
            entrypoint=self.entrypoint,
            repo_root=self.repo,
            device_request="cuda:2",
        )
        self.assertIn(self.image_ref, command)
        self.assertIn("device=2", command)
        self.assertTrue(any("src=" + str(self.model) in item and "readonly" in item for item in command))
        self.assertIn(f"HARNESS_PYTHON_BIN={self.harness_python}", command)
        self.assertIn("MODEL_PYTHON=python", command)
        self.assertIn("SURE_EVAL_APPROVED_MODEL_DIR=/workspace/model", command)
        self.assertIn("SURE_EVAL_APPROVED_RESULT_DIR=/sure-output", command)
        self.assertIn("SURE_EVAL_CACHE_DIR=/sure-output/.runtime/cache/sure-eval", command)
        self.assertNotEqual(provenance["harness_runtime"]["python_executable"], "python")
        self.assertFalse(provenance["host_python_fallback"])

    def test_local_command_preserves_declared_dataset_mount_target(self) -> None:
        binding = load_deployment_binding(self.model, "demo")
        real_dataset = self.root / "dataset-real"
        real_dataset.mkdir()
        dataset_alias = self.root / "dataset-alias"
        dataset_alias.symlink_to(real_dataset, target_is_directory=True)
        command, _ = build_local_container_command(
            surface={"env": {}},
            eval_input={
                "model": {"deployment_binding": binding},
                "runtime": {
                    "run_dir": str(self.output),
                    "harness_runtime": self._runtime_binding(),
                },
                "datasets": [{"source_root": str(dataset_alias)}],
            },
            control_run_dir=self.control,
            entrypoint=self.entrypoint,
            repo_root=self.repo,
            device_request="cpu",
        )
        self.assertIn(
            f"type=bind,src={real_dataset},dst={dataset_alias},readonly",
            command,
        )

    def test_local_command_injects_separate_evaluation_runtime(self) -> None:
        binding = load_deployment_binding(self.model, "demo")
        evaluation = {
            "runtime_id": "sure-evaluation-test",
            "python_executable": str(self.repo / "sure" / ".runtime" / "evaluation" / "bin" / "python"),
            "runtime_root": str(self.repo / "sure" / ".runtime" / "evaluation"),
            "manifest_path": str(self.repo / "sure" / ".runtime" / "evaluation" / "runtime-manifest.json"),
            "lock_sha256": "e" * 64,
            "engine_root": str(self.repo / "sure" / "external" / "sure-evaluation"),
        }
        with patch("container_execution.evaluation_runtime_from_eval_input", return_value=evaluation):
            command, provenance = build_local_container_command(
                surface={"env": {}},
                eval_input={
                    "model": {"deployment_binding": binding},
                    "runtime": {
                        "run_dir": str(self.output),
                        "harness_runtime": self._runtime_binding(),
                    },
                    "datasets": [],
                },
                control_run_dir=self.control,
                entrypoint=self.entrypoint,
                repo_root=self.repo,
                device_request="cpu",
            )
        self.assertIn(f"SURE_EVALUATION_PYTHON={evaluation['python_executable']}", command)
        self.assertIn("SURE_EVALUATION_RUNTIME_ID=sure-evaluation-test", command)
        self.assertEqual(provenance["evaluation_runtime"], evaluation)

    def test_vc_entrypoint_never_rewrites_model_runtime(self) -> None:
        path = self.control / "vc_entrypoint.sh"
        _write_entrypoint(
            path=path,
            volume_mount=f"{self.root}:{self.root}",
            container_image=self.image_ref,
            container_repo_root=str(self.repo),
            vc_partition="demo",
            vc_memory="16G",
            vc_gpus=1,
            vc_cpus=4,
            model_python_bin="python",
            model_pythonpath=[],
            run_evaluation_path=str(self.entrypoint),
            log_path=self.output / "vc.log",
            execution_requested="vc",
            device_request="cuda:0",
            device_actual="cuda:0",
            harness_python_bin=str(self.harness_python),
            harness_library_paths=[],
            harness_python_home="",
            entrypoint_env={
                "MODEL_DIR": "/workspace/model",
                "SURE_EVAL_APPROVED_MODEL_DIR": "/workspace/model",
                "RUN_DIR": "/sure-output",
                "SURE_EVAL_APPROVED_RESULT_DIR": "/sure-output",
                "SURE_EVAL_CACHE_DIR": "/sure-output/.runtime/cache/sure-eval",
            },
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn(self.image_ref, text)
        self.assertIn(f"export SURE_EVAL_CONTAINER_IMAGE={self.image_ref}", text)
        self.assertNotIn(".venv", text)
        self.assertNotIn("/usr/bin/python3", text)
        self.assertIn(f"export HARNESS_PYTHON_BIN={self.harness_python}", text)
        self.assertIn("export MODEL_PYTHON=python", text)
        self.assertIn("export SURE_EVAL_APPROVED_MODEL_DIR=/workspace/model", text)
        self.assertIn("export SURE_EVAL_APPROVED_RESULT_DIR=/sure-output", text)
        self.assertIn("export SURE_EVAL_CACHE_DIR=/sure-output/.runtime/cache/sure-eval", text)
        self.assertNotRegex(text, r"(?m)^\s*(mv|ln|rm)\b")

    def test_local_command_prefers_matching_image_harness_runtime(self) -> None:
        binding = load_deployment_binding(self.model, "demo")
        image_python = "/opt/sure-harness/test/bin/python"
        binding["container"]["harness_runtime"] = {
            **self._runtime_binding(),
            "python_executable": image_python,
            "manifest_path": "/opt/sure-harness/test/runtime-manifest.json",
            "runtime_root": "/opt/sure-harness/test",
            "execution_source": "approved_image",
        }
        command, provenance = build_local_container_command(
            surface={"env": {}},
            eval_input={
                "model": {"deployment_binding": binding},
                "runtime": {
                    "run_dir": str(self.output),
                    "harness_runtime": self._runtime_binding(),
                },
                "datasets": [],
            },
            control_run_dir=self.control,
            entrypoint=self.entrypoint,
            repo_root=self.repo,
            device_request="cpu",
        )
        self.assertIn(f"HARNESS_PYTHON_BIN={image_python}", command)
        self.assertFalse(provenance["harness_runtime_mounted_from_repo"])
        self.assertEqual(provenance["harness_runtime"]["execution_source"], "approved_image")

    def test_vc_memory_reads_only_the_approved_model_bundle(self) -> None:
        (self.model / "model.spec.yaml").write_text(
            "resources:\n  memory_gb: 24\nweights:\n  size_gb: 10\n",
            encoding="utf-8",
        )
        self.assertEqual(_approved_memory_gb(self.model), 29)


if __name__ == "__main__":
    unittest.main()
