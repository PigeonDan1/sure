#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deployment_contract import document_timestamp, resolve_model_dir
from finalize_model_bundle import ensure_safe_bundle_targets, finalize
from write_package_gate import write_package_gate
from write_runtime_inventory import write_inventory
from write_verdict import write_verdict


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
        for name in ("model.spec.yaml", "model.py", "server.py", "__init__.py", "validate.py"):
            (self.model_dir / name).write_text("# test\n", encoding="utf-8")
        (self.model_dir / "config.yaml").write_text(
            "model:\n  id: demo/asr\n"
            "task: ASR\n"
            "server:\n  command: [python, server.py]\n"
            "tools:\n  - name: transcribe_audio\n"
            "resources:\n  gpu: false\n",
            encoding="utf-8",
        )
        self.dockerfile = self.model_dir / "Dockerfile"
        self.dockerfile.write_text("FROM python:3.12\nWORKDIR /workspace/model\n", encoding="utf-8")
        fixture_dir = self.model_dir / "fixture" / "asr" / "smoke"
        fixture_dir.mkdir(parents=True)
        (fixture_dir / "sample.wav").write_bytes(b"RIFF-test")
        (fixture_dir / "gt.jsonl").write_text(
            json.dumps({"audio": "sample.wav", "text": "ground truth"}) + "\n",
            encoding="utf-8",
        )
        self.sample = self.run_artifacts / "container_sample.json"
        write_json(self.sample, {"text": "ok"})
        write_json(self.run_artifacts / "sample_output.json", {"text": "ok"})
        write_json(self.model_artifacts / "sample_output.json", {"text": "ok"})
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
        write_json(
            self.run_artifacts / "build_env_result.json",
            {
                "env_ready": True,
                "backend": "pip",
                "python_version": "3.12",
                "duration_seconds": 1.25,
            },
        )
        write_json(
            self.run_artifacts / "backend_choice.json",
            {"backend": "pip", "choice_reason": "test evidence"},
        )
        write_json(self.run_artifacts / "env_compat_result.json", {"compat_ok": True})
        for filename, pass_key in (
            ("import_result.json", "import_passed"),
            ("load_result.json", "load_passed"),
            ("infer_result.json", "infer_passed"),
            ("contract_result.json", "contract_passed"),
        ):
            write_json(
                self.run_artifacts / filename,
                {pass_key: True, "duration_ms": 1.0, "error": None},
            )
        weights_manifest = {"required": False, "weights_ready": True, "status": "fetched"}
        write_json(self.run_artifacts / "weights_manifest.json", weights_manifest)
        write_json(self.model_artifacts / "weights_manifest.json", weights_manifest)
        manifest = {
            "model_dir": str(self.model_dir),
            "timestamp": "2026-01-01T00:00:00+00:00",
            "status": "staged",
            "artifacts": {
                "required": {"model": {"path": "model.py"}},
                "conditional": {},
                "optional": {},
            },
        }
        write_json(self.run_artifacts / "artifact_manifest.json", manifest)
        write_json(self.model_artifacts / "artifact_manifest.json", manifest)
        for source in self.run_artifacts.glob("*.json"):
            shutil.copy2(source, self.model_artifacts / source.name)

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

    def test_finalizer_rejects_a_symlinked_model_artifacts_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_dir = root / "sure" / "models" / "demo"
            outside = root / "outside"
            model_dir.mkdir(parents=True)
            outside.mkdir()
            sentinel = outside / "keep.json"
            sentinel.write_text("keep\n", encoding="utf-8")
            (model_dir / "artifacts").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ValueError):
                ensure_safe_bundle_targets(model_dir, {"model_dir": str(model_dir)})

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

    def test_shared_model_resolver_accepts_trans_input(self) -> None:
        (self.run_artifacts / "model_input_resolved.json").unlink()
        write_json(self.run_artifacts / "trans_input_resolved.json", self.resolved)

        model_dir, resolved = resolve_model_dir(self.run_dir)

        self.assertEqual(model_dir, self.model_dir.resolve())
        self.assertEqual(resolved["model_name"], "demo__asr")

    def test_container_gate_requires_live_digest_inspect(self) -> None:
        proc = self.run_script(
            "check_container_package.py",
            self.run_artifacts / "docker_registry_result.json",
            env=self.fake_docker_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_fixture_gate_rejects_an_empty_annotation_value(self) -> None:
        fixture_dir = self.model_dir / "fixture" / "asr" / "smoke"
        (fixture_dir / "gt.jsonl").write_text(
            json.dumps({"audio": "sample.wav", "text": ""}) + "\n",
            encoding="utf-8",
        )
        manifest = {
            "model_dir": str(self.model_dir),
            "task_type": "asr",
            "staged_dir": str(fixture_dir),
            "gt_jsonl": str(fixture_dir / "gt.jsonl"),
            "samples": [{"audio": "sample.wav"}],
            "sample_count": 1,
        }
        produces = self.run_artifacts / "fixture_manifest.json"
        write_json(produces, manifest)

        proc = self.run_script("check_fixture.py", produces)

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("annotation field", proc.stderr)

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
        package = write_package_gate(self.run_dir, self.run_artifacts / "package_gate.json", self.model_dir)
        inventory = write_inventory(self.model_dir, self.run_artifacts / "runtime_inventory.json", self.run_dir)
        verdict = write_verdict(self.run_dir, self.run_artifacts / "verdict.json", self.model_dir)
        manifest = read_json(self.model_artifacts / "artifact_manifest.json")
        self.assertGreater(
            document_timestamp(package, "package_gate.json"),
            document_timestamp(manifest, "artifact_manifest.json"),
        )
        self.assertGreater(
            document_timestamp(inventory, "runtime_inventory.json"),
            document_timestamp(package, "package_gate.json"),
        )
        self.assertGreater(
            document_timestamp(verdict, "verdict.json"),
            document_timestamp(inventory, "runtime_inventory.json"),
        )

        write_json(
            self.run_artifacts / "package_gate.json",
            {"status": "passed", "generated_at": "2099-01-01T00:00:00+00:00", "forged": True},
        )
        write_json(
            self.run_artifacts / "runtime_inventory.json",
            {"status": "ready", "generated_at": "2099-01-01T00:00:01+00:00", "forged": True},
        )
        write_json(
            self.run_artifacts / "verdict.json",
            {"status": "passed", "timestamp": "2099-01-01T00:00:02+00:00", "forged": True},
        )
        output = self.run_artifacts / "deployment_ready.json"
        deployment = finalize(self.run_dir, output)
        self.assertEqual(deployment["status"], "ready")
        self.assertEqual(deployment["schema"], "sure.onboard.deployment_ready.v1")
        self.assertEqual(
            deployment["execution_policy"],
            {
                "container_only": True,
                "nfs_models_read_only": True,
                "host_python_fallback": False,
                "approved_image_override": False,
            },
        )
        self.assertNotIn("model_runtime", deployment)
        self.assertEqual(deployment["harness_runtime"]["runtime_id"], "sure-harness-test")
        finalized_manifest = read_json(self.model_artifacts / "artifact_manifest.json")
        regenerated_package = read_json(self.model_artifacts / "package_gate.json")
        regenerated_inventory = read_json(self.model_artifacts / "runtime_inventory.json")
        regenerated_verdict = read_json(self.model_artifacts / "verdict.json")
        self.assertEqual(finalized_manifest["model_dir"], ".")
        self.assertNotIn("forged", regenerated_package)
        self.assertNotIn("forged", regenerated_inventory)
        self.assertNotIn("forged", regenerated_verdict)
        timeline = [
            document_timestamp(finalized_manifest, "artifact_manifest.json"),
            document_timestamp(regenerated_package, "package_gate.json"),
            document_timestamp(regenerated_inventory, "runtime_inventory.json"),
            document_timestamp(regenerated_verdict, "verdict.json"),
            document_timestamp(deployment, "deployment_ready.json"),
        ]
        self.assertEqual(timeline, sorted(timeline))
        self.assertEqual(len(set(timeline)), len(timeline))
        proc = self.run_script("check_finalized_bundle.py", output)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        broken_inventory = json.loads((self.run_artifacts / "runtime_inventory.json").read_text())
        broken_inventory["harness_runtime"].pop("runtime_root")
        write_json(self.run_artifacts / "runtime_inventory.json", broken_inventory)
        repaired = finalize(self.run_dir, output)
        self.assertEqual(repaired["status"], "ready")
        self.assertIn("runtime_root", read_json(self.run_artifacts / "runtime_inventory.json")["harness_runtime"])

        self.validation["harness_runtime"]["runtime_root"] = "/opt/sure-harness/other"
        write_json(self.run_artifacts / "docker_validation.json", self.validation)
        with self.assertRaisesRegex(ValueError, "manifest_path"):
            finalize(self.run_dir, output)

    def test_package_writer_uses_staged_model_evidence_instead_of_mutated_run_copy(self) -> None:
        write_json(
            self.run_artifacts / "package_gate.json",
            {
                "schema": "sure.onboard.package_gate.v2",
                "status": "passed",
                "generated_at": "2099-01-01T00:00:00+00:00",
            },
        )
        write_json(
            self.run_artifacts / "infer_result.json",
            {"infer_passed": False, "duration_ms": 1.0, "error": "real failure"},
        )
        package = write_package_gate(
            self.run_dir,
            self.run_artifacts / "package_gate.json",
            self.model_dir,
        )

        self.assertEqual(package["status"], "passed")
        self.assertTrue(read_json(self.model_artifacts / "infer_result.json")["infer_passed"])

    def test_finalized_gate_rejects_deployment_not_later_than_verdict(self) -> None:
        output = self.run_artifacts / "deployment_ready.json"
        deployment = finalize(self.run_dir, output)
        verdict = read_json(self.model_artifacts / "verdict.json")
        deployment["generated_at"] = verdict["timestamp"]
        write_json(output, deployment)
        write_json(self.model_artifacts / "deployment_ready.json", deployment)

        proc = self.run_script("check_finalized_bundle.py", output)

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("deployment_ready.json timestamp must be later", proc.stderr)

    def test_required_weights_fall_back_to_model_local_checkpoint_root(self) -> None:
        checkpoint_root = self.model_dir / "checkpoints" / "demo"
        checkpoint_root.mkdir(parents=True)
        (checkpoint_root / "weights.bin").write_bytes(b"weights")
        weights = {
            "required": True,
            "weights_ready": True,
            "status": "fetched",
            "checkpoint_root": str(checkpoint_root),
        }
        write_json(self.run_artifacts / "weights_manifest.json", weights)
        write_json(self.model_artifacts / "weights_manifest.json", weights)

        deployment = finalize(self.run_dir, self.run_artifacts / "deployment_ready.json")

        self.assertIn("checkpoints/demo/weights.bin", deployment["required_artifact_sha256"])

    def test_required_weights_without_model_local_root_block_finalize(self) -> None:
        weights = {"required": True, "weights_ready": True, "status": "fetched"}
        write_json(self.run_artifacts / "weights_manifest.json", weights)
        write_json(self.model_artifacts / "weights_manifest.json", weights)

        with self.assertRaisesRegex(ValueError, "required weights have no model-local root"):
            finalize(self.run_dir, self.run_artifacts / "deployment_ready.json")

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
