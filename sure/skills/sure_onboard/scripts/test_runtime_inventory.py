#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_runtime_inventory
import stage_model_artifacts
from write_runtime_inventory import write_inventory


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class RuntimeInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / "run"
        self.run_artifacts = self.run_dir / "artifacts"
        self.model_dir = self.root / "sure" / "models" / "demo__asr"
        self.artifacts = self.model_dir / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.run_artifacts.mkdir(parents=True)
        for name in stage_model_artifacts.CORE_FILES:
            (self.model_dir / name).write_text("# test\n", encoding="utf-8")
        (self.model_dir / "Dockerfile").write_text("FROM python:3.12\nWORKDIR /workspace/model\n", encoding="utf-8")
        (self.model_dir / "config.yaml").write_text(
            "task: asr\nmodel:\n  id: demo/asr\n  backend: uv\n"
            "server:\n  command: [.venv/bin/python, server.py]\n"
            "tools:\n  - name: asr_transcribe\nresources:\n  gpu: true\n",
            encoding="utf-8",
        )
        (self.model_dir / ".venv" / "bin").mkdir(parents=True)
        (self.model_dir / ".venv" / "bin" / "python").symlink_to(sys.executable)
        (self.model_dir / "requirements.lock").write_text("torch\n", encoding="utf-8")
        resolved = {
            "model_id": "demo/asr",
            "model_name": "demo__asr",
            "model_dir": str(self.model_dir),
            "task_type": "asr",
            "deployment_type": "local",
            "package_profile": "docker-registry",
        }
        write_json(self.run_artifacts / "model_input_resolved.json", resolved)
        write_json(
            self.run_artifacts / "build_env_result.json",
            {
                "env_ready": True,
                "backend": "uv",
                "python_version": "3.12.4",
                "python_executable": str(self.model_dir / ".venv" / "bin" / "python"),
                "lockfile_path": str(self.model_dir / "requirements.lock"),
            },
        )
        write_json(self.run_artifacts / "weights_manifest.json", {"weights_ready": True})
        digest = "sha256:" + "a" * 64
        image = "registry.example.com/sure/demo:v1"
        image_ref = "registry.example.com/sure/demo@" + digest
        write_json(
            self.run_artifacts / "docker_build_result.json",
            {
                "status": "passed",
                "base_image": "python:3.12",
                "target_image": image,
                "target_image_digest": digest,
                "target_image_ref": image_ref,
                "dockerfile_path": "Dockerfile",
                "dockerfile_sha256": hashlib.sha256((self.model_dir / "Dockerfile").read_bytes()).hexdigest(),
            },
        )
        write_json(
            self.run_artifacts / "docker_validation.json",
            {
                "status": "passed",
                "target_image": image,
                "target_image_digest": digest,
                "target_image_ref": image_ref,
                "checks": {name: True for name in ("import", "load", "infer", "contract", "bounded_fixture_inference")},
                "model_runtime": {
                    "runtime_type": "model_python",
                    "python_executable": "python",
                    "python_version": "3.12.4",
                    "checks": {name: True for name in ("import", "load", "infer", "contract", "bounded_fixture_inference")},
                },
                "harness_runtime": {
                    "schema": "sure.harness.runtime.binding.v1",
                    "runtime_id": "sure-harness-test",
                    "runtime_type": "harness_python",
                    "python_executable": "/opt/sure-harness/test/bin/python",
                    "python_version": "3.11.5",
                    "python_abi": "cp311",
                    "lock_sha256": "c" * 64,
                    "manifest_path": "/opt/sure-harness/test/runtime-manifest.json",
                    "runtime_root": "/opt/sure-harness/test",
                    "materialization": "image_copy",
                    "checks": {
                        name: True
                        for name in (
                            "imports",
                            "dataset_prepare",
                            "server_orchestration",
                            "prediction",
                            "prediction_validation",
                        )
                    },
                },
                "runtime_separation": {"distinct_executables": True},
                "runtime": {
                    "python_executable": "python",
                    "working_dir": "/workspace/model",
                    "server_command": ["python", "server.py"],
                },
            },
        )
        write_json(
            self.run_artifacts / "docker_registry_result.json",
            {
                "status": "passed",
                "target_image": image,
                "target_image_digest": digest,
                "target_image_ref": image_ref,
                "push": {"status": "passed"},
                "pull_verify": {"status": "passed", "digest": digest},
            },
        )
        write_json(
            self.run_artifacts / "package_gate.json",
            {
                "schema": "sure.onboard.package_gate.v2",
                "status": "passed",
                "package_profile": "docker-registry",
                "readiness": {
                    "local_ready": True,
                    "container_ready": True,
                    "docker_ready": True,
                    "registry_ready": True,
                    "bundle_ready": True,
                },
            },
        )

    def test_v2_inventory_binds_digest_and_disables_host_python(self) -> None:
        output = self.run_artifacts / "runtime_inventory.json"
        inventory = write_inventory(self.model_dir, output, self.run_dir)
        self.assertEqual(inventory["schema"], "sure.onboard.runtime_inventory.v2")
        self.assertEqual(inventory["status"], "ready")
        self.assertFalse(inventory["local_runtime"]["eligible_for_eval"])
        self.assertFalse(inventory["policy"]["host_python_fallback"])
        self.assertEqual(inventory["model_runtime"]["python_executable"], "python")
        self.assertEqual(inventory["harness_runtime"]["runtime_id"], "sure-harness-test")
        self.assertNotEqual(
            inventory["model_runtime"]["python_executable"],
            inventory["harness_runtime"]["python_executable"],
        )
        self.assertTrue(inventory["container_runtime"]["target_image_ref"].endswith("@sha256:" + "a" * 64))
        self.assertEqual(inventory["container_runtime"]["tool_names"], ["asr_transcribe"])
        self.assertEqual(output.read_bytes(), (self.artifacts / "runtime_inventory.json").read_bytes())

    def test_missing_package_gate_blocks_inventory(self) -> None:
        (self.run_artifacts / "package_gate.json").unlink()
        with self.assertRaises(ValueError):
            write_inventory(self.model_dir, self.run_artifacts / "runtime_inventory.json", self.run_dir)

    def test_package_none_publishes_python_eval_runtime(self) -> None:
        resolved_path = self.run_artifacts / "model_input_resolved.json"
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        resolved["package_profile"] = "none"
        write_json(resolved_path, resolved)
        write_json(
            self.run_artifacts / "package_gate.json",
            {
                "schema": "sure.onboard.package_gate.v2",
                "status": "passed",
                "package_profile": "none",
                "readiness": {
                    "local_ready": True,
                    "container_ready": False,
                    "docker_ready": False,
                    "registry_ready": False,
                    "bundle_ready": False,
                },
            },
        )

        inventory = write_inventory(
            self.model_dir,
            self.run_artifacts / "runtime_inventory.json",
            self.run_dir,
        )

        self.assertEqual(inventory["status"], "ready")
        self.assertEqual(inventory["policy"]["eval_runtime"], "python_only")
        self.assertTrue(inventory["local_runtime"]["eligible_for_eval"])
        self.assertEqual(inventory["local_runtime"]["path_scope"], "model_relative")
        self.assertEqual(inventory["local_runtime"]["python_executable"], ".venv/bin/python")
        self.assertEqual(inventory["local_runtime"]["server_command"][0], ".venv/bin/python")
        self.assertEqual(inventory["local_runtime"]["tool_names"], ["asr_transcribe"])
        self.assertFalse(inventory["container_runtime"]["required"])
        for name in ("docker_build_result.json", "docker_validation.json", "docker_registry_result.json"):
            (self.run_artifacts / name).unlink()
            self.assertFalse((self.artifacts / name).exists())
        with (
            patch.object(
                check_runtime_inventory,
                "load_site_policy",
                return_value={"policy": {"execution": {"local_runtimes": ["python", "container"]}}},
            ),
            patch.object(
                sys,
                "argv",
                [
                    "check_runtime_inventory.py",
                    "--run-dir",
                    str(self.run_dir),
                    "--produces",
                    str(self.run_artifacts / "runtime_inventory.json"),
                ],
            ),
        ):
            self.assertEqual(check_runtime_inventory.main(), 0)

    def test_stage_model_artifacts_does_not_write_inventory_early(self) -> None:
        for name in stage_model_artifacts.REQUIRED_RUN_ARTIFACTS:
            path = self.run_artifacts / name
            if not path.exists():
                write_json(path, {"ok": True, "name": name})
        produces = self.run_artifacts / "artifact_manifest.json"
        rc = stage_model_artifacts.main_with_args(
            ["--run-dir", str(self.run_dir), "--produces", str(produces), "--allow-missing-run-artifacts"]
        )
        self.assertEqual(rc, 0)
        self.assertFalse((self.artifacts / "runtime_inventory.json").exists())


if __name__ == "__main__":
    unittest.main()
