#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from container_execution import build_local_container_command
from deployment_binding import DeploymentBindingError, _portable_relative, load_deployment_binding
from check_run_report import _submitted_image_error
from run_vc_execution import (
    _approved_memory_gb,
    _build_vc_volume_mounts,
    _job_name,
    _normalize_job_name,
    _surface_env_for_container,
    _write_entrypoint,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DeploymentBindingTests(unittest.TestCase):
    def test_portable_path_rejects_the_bundle_root(self) -> None:
        with self.assertRaises(DeploymentBindingError):
            _portable_relative(".", "test path")

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
            # Both are written by run_vc_execution and the schema is closed, so
            # an undeclared key makes the SUBMIT_EXECUTION gate refuse the
            # artifact after `vc submit` has already run.
            "evaluation_runtime",
            "surface_env_refused",
        }

        self.assertTrue(expected.issubset(properties))

    def test_route_plan_schema_declares_the_node_environment_blockers(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "evaluation_route_plan.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertFalse(schema["additionalProperties"])
        self.assertIn("node_environment_blockers", schema["properties"])

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
        self.projection = self.root / "dataset-projection"
        (self.projection / "sure_benchmark" / "jsonl").mkdir(parents=True)
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
        write_json(
            self.artifacts / "artifact_manifest.json",
            {
                "schema": "sure.onboard.artifact_manifest.v1",
                "status": "finalized",
                "model_dir": ".",
                "artifacts": {
                    "required": {
                        "runtime_inventory": {"path": "artifacts/runtime_inventory.json"},
                        "package_gate": {"path": "artifacts/package_gate.json"},
                    }
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
        self.assertEqual(binding["schema"], "sure.eval.deployment_binding.v2")
        self.assertEqual(binding["target_image_ref"], self.image_ref)
        self.assertTrue(binding["container"]["model_mount"]["read_only"])

    def test_derives_legacy_image_runtime_root_from_manifest(self) -> None:
        inventory = json.loads((self.artifacts / "runtime_inventory.json").read_text())
        legacy_binding = self._runtime_binding()
        legacy_binding.pop("runtime_root")
        inventory["harness_runtime"] = {"required": True, **legacy_binding}
        write_json(self.artifacts / "runtime_inventory.json", inventory)

        marker = json.loads((self.artifacts / "deployment_ready.json").read_text())
        marker["harness_runtime"] = legacy_binding
        hashes = marker["required_artifact_sha256"]
        hashes["artifacts/runtime_inventory.json"] = sha256(self.artifacts / "runtime_inventory.json")
        marker["bundle_identity_sha256"] = hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        write_json(self.artifacts / "deployment_ready.json", marker)

        binding = load_deployment_binding(self.model, "demo")
        self.assertEqual(binding["container"]["harness_runtime"]["runtime_root"], str(self.harness_runtime))

    def test_rejects_explicit_harness_runtime_root_mismatch(self) -> None:
        inventory = json.loads((self.artifacts / "runtime_inventory.json").read_text())
        declared = {"required": True, **self._runtime_binding(), "runtime_root": str(self.repo / "wrong")}
        inventory["harness_runtime"] = declared
        write_json(self.artifacts / "runtime_inventory.json", inventory)

        marker = json.loads((self.artifacts / "deployment_ready.json").read_text())
        marker["harness_runtime"] = {key: value for key, value in declared.items() if key != "required"}
        hashes = marker["required_artifact_sha256"]
        hashes["artifacts/runtime_inventory.json"] = sha256(self.artifacts / "runtime_inventory.json")
        marker["bundle_identity_sha256"] = hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        write_json(self.artifacts / "deployment_ready.json", marker)

        with self.assertRaisesRegex(DeploymentBindingError, "disagrees with runtime_root"):
            load_deployment_binding(self.model, "demo")

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

    def test_complete_integrity_profile_rejects_a_self_consistent_but_incomplete_manifest(self) -> None:
        marker = json.loads((self.artifacts / "deployment_ready.json").read_text())
        marker["integrity_profile"] = "manifest-complete-v1"
        write_json(self.artifacts / "deployment_ready.json", marker)

        with self.assertRaisesRegex(DeploymentBindingError, "mandatory deployment sidecar|mandatory core"):
            load_deployment_binding(self.model, "demo")

    def test_local_command_uses_digest_and_read_only_model(self) -> None:
        binding = load_deployment_binding(self.model, "demo")
        command, provenance = build_local_container_command(
            surface={
                "env": {
                    "TOOL_NAME": "transcribe_audio",
                    "SURE_EVAL_NODE_LOCAL_PYTHON": "/usr/bin/python3.11",
                }
            },
            eval_input={
                "model": {"deployment_binding": binding},
                "runtime": {
                    "run_dir": str(self.output),
                    "harness_runtime": self._runtime_binding(),
                    "dataset_projection": {"host_root": str(self.projection)},
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
        self.assertIn(f"SURE_EVAL_NODE_LOCAL_PYTHON={self.harness_python}", command)
        self.assertNotIn("SURE_EVAL_NODE_LOCAL_PYTHON=/usr/bin/python3.11", command)
        self.assertIn("MODEL_PYTHON=python", command)
        self.assertIn("SURE_EVAL_APPROVED_MODEL_DIR=/workspace/model", command)
        self.assertIn("SURE_EVAL_APPROVED_RESULT_DIR=/sure-output", command)
        self.assertIn("SURE_EVAL_CACHE_DIR=/sure-output/.runtime/cache/sure-eval", command)
        self.assertIn(f"SURE_EVAL_DATASETS_ROOT={self.projection}", command)
        self.assertIn(
            f"type=bind,src={self.projection},dst={self.projection}",
            command,
        )
        self.assertEqual(
            provenance["dataset_projection_mount"],
            {"source": str(self.projection), "target": str(self.projection), "read_only": False},
        )
        self.assertNotEqual(provenance["harness_runtime"]["python_executable"], "python")
        self.assertEqual(
            provenance["evaluation_node_runtime"]["python_executable"],
            str(self.harness_python),
        )
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

    def test_local_command_does_not_remount_projection_jsonl_read_only(self) -> None:
        binding = load_deployment_binding(self.model, "demo")
        jsonl_path = self.projection / "sure_benchmark" / "jsonl" / "demo.jsonl"
        command, _ = build_local_container_command(
            surface={"env": {}},
            eval_input={
                "model": {"deployment_binding": binding},
                "runtime": {
                    "run_dir": str(self.output),
                    "harness_runtime": self._runtime_binding(),
                    "dataset_projection": {"host_root": str(self.projection)},
                },
                "datasets": [{"jsonl_path": str(jsonl_path)}],
            },
            control_run_dir=self.control,
            entrypoint=self.entrypoint,
            repo_root=self.repo,
            device_request="cpu",
        )
        projection_mounts = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "--mount" and str(self.projection) in command[index + 1]
        ]
        self.assertEqual(
            projection_mounts,
            [f"type=bind,src={self.projection},dst={self.projection}"],
        )

    def test_vc_mounts_keep_models_and_sources_read_only(self) -> None:
        dataset_source = self.root / "source-dataset"
        dataset_source.mkdir()
        dataset_alias = self.root / "source-dataset-alias"
        dataset_alias.symlink_to(dataset_source, target_is_directory=True)
        volume = _build_vc_volume_mounts(
            primary=f"{self.repo}:{self.repo}",
            model_dir=self.model,
            model_target="/workspace/model",
            result_source=self.output,
            result_target="/sure-output",
            dataset_source_roots=[str(dataset_alias)],
            dataset_projection_root=self.projection,
        )
        self.assertIn(f"{self.model}:{self.model}:ro", volume)
        self.assertIn(f"{self.model}:/workspace/model:ro", volume)
        self.assertIn(f"{dataset_alias}:{dataset_alias}:ro", volume)
        self.assertIn(f"{self.projection}:{self.projection}", volume)
        self.assertNotIn(f"{self.projection}:{self.projection}:ro", volume)
        self.assertIn(f"{self.output}:/sure-output", volume)

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

    def test_local_command_refuses_surface_declared_path_and_provenance(self) -> None:
        # The agent writes execution_surface.json, so its env is a request, not
        # a fact. Run 20260828-142343 declared a PATH here whose first entry
        # held a fake git, and the container's provenance check answered from
        # it; another run declared GIT_CONFIG_* to make git accept a checkout it
        # would otherwise refuse. The mode of the run stays the agent's to pick.
        binding = load_deployment_binding(self.model, "demo")
        evaluation = {
            "runtime_id": "sure-evaluation-test",
            "python_executable": str(self.repo / "sure" / ".runtime" / "evaluation" / "bin" / "python"),
            "runtime_root": str(self.repo / "sure" / ".runtime" / "evaluation"),
            "manifest_path": str(self.repo / "sure" / ".runtime" / "evaluation" / "runtime-manifest.json"),
            "lock_sha256": "e" * 64,
            "engine_root": str(self.repo / "sure" / "external" / "sure-evaluation"),
        }
        surface = {
            "env": {
                "PATH": "/tmp/agent/bin:/usr/bin",
                "LD_PRELOAD": "/tmp/agent/hook.so",
                "GIT_CONFIG_KEY_0": "safe.directory",
                "SURE_EVALUATION_RUNTIME_ID": "forged-runtime",
                "SURE_HARNESS_RUNTIME_ROOT": "/tmp/agent/harness",
                "EVALUATION_BACKEND": "external",
                "REPAIR_INVALID_ONLY": "1",
                # Not refused, but not the run's to choose either: the templates
                # reach every gate script through "$REPO_ROOT/scripts/".
                "REPO_ROOT": "/tmp/agent/skill",
            }
        }
        with patch("container_execution.evaluation_runtime_from_eval_input", return_value=evaluation):
            command, provenance = build_local_container_command(
                surface=surface,
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
        joined = " ".join(command)
        self.assertNotIn("/tmp/agent", joined)
        self.assertNotIn("forged-runtime", joined)
        self.assertNotIn("safe.directory", joined)
        # The run's own mode is still the agent's to declare.
        self.assertIn("EVALUATION_BACKEND=external", command)
        self.assertIn("REPAIR_INVALID_ONLY=1", command)
        # The host's own value survives, and every refusal leaves a trace.
        self.assertIn("SURE_EVALUATION_RUNTIME_ID=sure-evaluation-test", command)
        self.assertIn(f"REPO_ROOT={self.repo / 'sure' / 'skills' / 'sure_eval'}", command)
        self.assertEqual(
            sorted(provenance["surface_env_refused"]),
            [
                "GIT_CONFIG_KEY_0",
                "LD_PRELOAD",
                "PATH",
                "SURE_EVALUATION_RUNTIME_ID",
                "SURE_HARNESS_RUNTIME_ROOT",
            ],
        )

    def test_vc_entrypoint_refuses_the_same_keys_as_the_local_container(self) -> None:
        # The vc route had its own copy of the filter, so a key blocked on one
        # route reached the container on the other.
        values = _surface_env_for_container(
            {
                "env": {
                    "PATH": "/tmp/agent/bin",
                    "SURE_EVALUATION_LOCK_SHA256": "f" * 64,
                    "TOOL_NAME": "transcribe_audio",
                }
            },
            "/workspace",
        )
        self.assertEqual(values, {"TOOL_NAME": "transcribe_audio"})

    def test_vc_entrypoint_lets_the_harness_have_the_last_word(self) -> None:
        # The surface exports were written after the harness block, so a key
        # the harness had already decided was simply re-exported by whatever
        # the surface said. SURE_EVAL_NODE_LOCAL_PYTHON carries a skip for
        # exactly this, which is one key patched and the rest left open.
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
            model_python_bin="/opt/model/bin/python",
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
                "SURE_EVAL_EXECUTION_PATH": "local_bash",
                "MODEL_PYTHON": "/tmp/agent/python",
                "RUN_DIR": "/sure-output",
            },
            submission_token="d" * 32,
            terminal_status_path=self.control / "vc_terminal_status.json",
        )
        text = path.read_text(encoding="utf-8")

        for key, harness_value in (
            ("SURE_EVAL_EXECUTION_PATH", "vc_submit"),
            ("MODEL_PYTHON", "/opt/model/bin/python"),
        ):
            exports = [line for line in text.splitlines() if line.startswith(f"export {key}=")]
            self.assertTrue(exports, key)
            self.assertIn(harness_value, exports[-1])
        # A key only the surface knows about still arrives.
        self.assertIn("export RUN_DIR=", text)

    def test_vc_entrypoint_never_rewrites_model_runtime(self) -> None:
        path = self.control / "vc_entrypoint.sh"
        terminal_status = self.control / "vc_terminal_status.json"
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
                "SURE_EVAL_NODE_LOCAL_PYTHON": "/usr/bin/python3.11",
            },
            submission_token="d" * 32,
            terminal_status_path=terminal_status,
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn(self.image_ref, text)
        self.assertIn(f"export SURE_EVAL_CONTAINER_IMAGE={self.image_ref}", text)
        self.assertNotIn(".venv", text)
        self.assertNotIn("/usr/bin/python3", text)
        self.assertIn(f"export HARNESS_PYTHON_BIN={self.harness_python}", text)
        self.assertIn(f"export SURE_EVAL_NODE_LOCAL_PYTHON={self.harness_python}", text)
        self.assertNotIn("export SURE_EVAL_NODE_LOCAL_PYTHON=/usr/bin/python3.11", text)
        self.assertIn("export MODEL_PYTHON=python", text)
        self.assertIn("export SURE_EVAL_APPROVED_MODEL_DIR=/workspace/model", text)
        self.assertIn("export SURE_EVAL_APPROVED_RESULT_DIR=/sure-output", text)
        self.assertIn("export SURE_EVAL_CACHE_DIR=/sure-output/.runtime/cache/sure-eval", text)
        self.assertIn("sure.eval.vc_terminal_status.v1", text)
        self.assertIn("trap _sure_eval_write_terminal EXIT", text)
        mutation_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().split(" ", 1)[0] in {"mv", "ln", "rm"}
        ]
        self.assertEqual(
            mutation_lines,
            ['mv -f -- "$terminal_tmp" "$_SURE_EVAL_TERMINAL_STATUS"'],
        )
        syntax = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        completed = subprocess.run(
            ["bash", str(path)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "VC_JOB_ID": "job-test"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        sentinel = json.loads(terminal_status.read_text(encoding="utf-8"))
        self.assertEqual(sentinel["submission_token"], "d" * 32)
        self.assertEqual(sentinel["job_status"], "succeeded")
        self.assertEqual(sentinel["exit_code"], 0)

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
