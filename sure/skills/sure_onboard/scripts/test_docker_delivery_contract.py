#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from finalize_model_bundle import finalize


SCRIPT_DIR = Path(__file__).resolve().parent


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class DockerDeliveryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / "run"
        self.run_artifacts = self.run_dir / "artifacts"
        self.model_dir = self.root / "sure" / "models" / "demo__asr"
        self.model_artifacts = self.model_dir / "artifacts"
        self.run_artifacts.mkdir(parents=True)
        self.model_artifacts.mkdir(parents=True)
        for name in ("model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py", "config.yaml"):
            (self.model_dir / name).write_text("# test\n", encoding="utf-8")
        self.dockerfile = self.model_dir / "Dockerfile"
        self.dockerfile.write_text("FROM python:3.12\nWORKDIR /workspace/model\n", encoding="utf-8")
        self.sample = self.run_artifacts / "container_sample.json"
        write_json(self.sample, {"text": "ok"})
        self.image = "registry.example.com/sure/demo:v1"
        self.digest = "sha256:" + "b" * 64
        self.image_ref = "registry.example.com/sure/demo@" + self.digest
        self.resolved = {
            "model_id": "demo/asr",
            "model_name": "demo__asr",
            "model_dir": str(self.model_dir),
            "repo_url": "https://example.com/demo.git",
            "task_type": "asr",
            "deployment_type": "local",
            "package_profile": "docker-registry",
        }
        write_json(self.run_artifacts / "model_input_resolved.json", self.resolved)
        self.build = {
            "schema": "sure.onboard.docker_build_result.v2",
            "status": "passed",
            "dockerfile_path": "Dockerfile",
            "dockerfile_sha256": hashlib.sha256(self.dockerfile.read_bytes()).hexdigest(),
            "base_image": "python:3.12",
            "target_image": self.image,
            "target_image_digest": self.digest,
            "target_image_ref": self.image_ref,
        }
        self.validation = {
            "schema": "sure.onboard.docker_validation.v2",
            "status": "passed",
            "target_image": self.image,
            "target_image_digest": self.digest,
            "target_image_ref": self.image_ref,
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
            "sample_output_path": str(self.sample),
            "sample_output_sha256": hashlib.sha256(self.sample.read_bytes()).hexdigest(),
            "runtime": {"python_executable": "python", "working_dir": "/workspace/model", "server_command": ["python", "server.py"]},
        }
        self.registry = {
            "schema": "sure.onboard.docker_registry_result.v2",
            "status": "passed",
            "target_image": self.image,
            "target_image_digest": self.digest,
            "target_image_ref": self.image_ref,
            "push": {"status": "passed"},
            "pull_verify": {"status": "passed", "digest": self.digest},
        }
        write_json(self.run_artifacts / "docker_build_result.json", self.build)
        write_json(self.run_artifacts / "docker_validation.json", self.validation)
        write_json(self.run_artifacts / "docker_registry_result.json", self.registry)

    def run_script(self, name: str, produces: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT_DIR / name), "--run-dir", str(self.run_dir), "--produces", str(produces)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def fake_docker_env(self) -> dict[str, str]:
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            f"if [ \"$1\" = image ]; then printf '%s\\n' '[\"{self.image_ref}\"]'; exit 0; fi\n"
            "if [ \"$1\" = run ]; then printf '%s\\n' '/runtime/python'; exit 0; fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["SURE_HARNESS_RUNTIME_ID"] = "sure-harness-test"
        env["SURE_HARNESS_LOCK_SHA256"] = "c" * 64
        return env

    def test_container_gate_requires_live_digest_inspect(self) -> None:
        proc = self.run_script(
            "check_container_package.py",
            self.run_artifacts / "docker_registry_result.json",
            env=self.fake_docker_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_container_gate_derives_legacy_runtime_root(self) -> None:
        self.validation["harness_runtime"].pop("runtime_root")
        write_json(self.run_artifacts / "docker_validation.json", self.validation)
        proc = self.run_script(
            "check_container_package.py",
            self.run_artifacts / "docker_registry_result.json",
            env=self.fake_docker_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_container_gate_rejects_runtime_root_outside_manifest_parent(self) -> None:
        self.validation["harness_runtime"]["runtime_root"] = "/opt/sure-harness/other"
        write_json(self.run_artifacts / "docker_validation.json", self.validation)
        proc = self.run_script(
            "check_container_package.py",
            self.run_artifacts / "docker_registry_result.json",
            env=self.fake_docker_env(),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("manifest_path", proc.stderr)

    def test_container_gate_rejects_digest_disagreement(self) -> None:
        bad = dict(self.registry)
        bad["target_image_digest"] = "sha256:" + "c" * 64
        write_json(self.run_artifacts / "docker_registry_result.json", bad)
        proc = self.run_script(
            "check_container_package.py",
            self.run_artifacts / "docker_registry_result.json",
            env=self.fake_docker_env(),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("disagrees", proc.stderr)

    def test_finalizer_writes_portable_marker_and_hashes(self) -> None:
        package = {
            "schema": "sure.onboard.package_gate.v2",
            "status": "passed",
            "package_profile": "docker-registry",
            "model_dir": str(self.model_dir),
            "artifact_manifest_path": str(self.model_artifacts / "artifact_manifest.json"),
            "readiness": {
                "local_ready": True,
                "container_ready": True,
                "docker_ready": True,
                "registry_ready": True,
                "bundle_ready": True,
            },
        }
        inventory = {
            "schema": "sure.onboard.runtime_inventory.v2",
            "status": "ready",
            "model": {"name": "demo__asr", "deployment_type": "local", "bundle_root": "."},
            "local_runtime": {"purpose": "evidence", "eligible_for_eval": False},
            "model_runtime": self.validation["model_runtime"],
            "harness_runtime": {
                "required": True,
                **self.validation["harness_runtime"],
            },
            "container_runtime": {
                "required": True,
                "target_image": self.image,
                "target_image_digest": self.digest,
                "target_image_ref": self.image_ref,
                "mount_policy": {"nfs_models_read_only": True},
            },
            "weights": {},
            "readiness": package["readiness"],
            "evidence": {},
            "policy": {
                "eval_runtime": "container_only",
                "host_python_fallback": False,
                "image_override_allowed": False,
                "nfs_models_mutable_by_eval": False,
            },
        }
        verdict = {"status": "passed"}
        manifest = {
            "model_dir": str(self.model_dir),
            "artifacts": {"required": {"model": {"path": "model.py"}}, "conditional": {}, "optional": {}},
        }
        for name, value in (
            ("package_gate.json", package),
            ("runtime_inventory.json", inventory),
            ("verdict.json", verdict),
        ):
            write_json(self.run_artifacts / name, value)
        write_json(self.model_artifacts / "artifact_manifest.json", manifest)
        output = self.run_artifacts / "deployment_ready.json"
        deployment = finalize(self.run_dir, output)
        self.assertEqual(deployment["status"], "ready")
        self.assertEqual(deployment["harness_runtime"]["runtime_id"], "sure-harness-test")
        self.assertEqual(read_json(self.model_artifacts / "artifact_manifest.json")["model_dir"], ".")
        proc = self.run_script("check_finalized_bundle.py", output)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        broken_inventory = json.loads((self.run_artifacts / "runtime_inventory.json").read_text())
        broken_inventory["harness_runtime"].pop("runtime_root")
        write_json(self.run_artifacts / "runtime_inventory.json", broken_inventory)
        with self.assertRaisesRegex(ValueError, "runtime_root is missing"):
            finalize(self.run_dir, output)

    def test_container_gate_rejects_harness_model_alias(self) -> None:
        self.validation["harness_runtime"]["python_executable"] = "python"
        write_json(self.run_artifacts / "docker_validation.json", self.validation)
        proc = self.run_script(
            "check_container_package.py",
            self.run_artifacts / "docker_registry_result.json",
            env=self.fake_docker_env(),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("distinct", proc.stderr)

    def test_container_gate_rejects_harness_lock_mismatch(self) -> None:
        self.validation["harness_runtime"]["lock_sha256"] = "d" * 64
        write_json(self.run_artifacts / "docker_validation.json", self.validation)
        proc = self.run_script(
            "check_container_package.py",
            self.run_artifacts / "docker_registry_result.json",
            env=self.fake_docker_env(),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("lock differs", proc.stderr)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
