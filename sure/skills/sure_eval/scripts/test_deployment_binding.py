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
from deployment_binding import (
    COMMON_MANDATORY_SIDECARS,
    CORE_BUNDLE_FILES,
    DeploymentBindingError,
    _mandatory_integrity_paths,
    _portable_relative,
    _require_declared_integrity_profile,
    _validate_complete_manifest,
    load_deployment_binding,
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
                "generated_at": "2026-08-01T00:00:00+00:00",
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

    def _rewrite_marker(self, **fields: object) -> None:
        marker = json.loads((self.artifacts / "deployment_ready.json").read_text())
        marker.update(fields)
        write_json(self.artifacts / "deployment_ready.json", marker)

    def test_marker_sealed_at_the_cutoff_must_declare_an_integrity_profile(self) -> None:
        self._rewrite_marker(generated_at="2026-09-01T00:00:00+00:00")

        with self.assertRaisesRegex(DeploymentBindingError, "integrity_profile"):
            load_deployment_binding(self.model, "demo")

    def test_marker_with_an_unreadable_timestamp_may_not_fall_back_to_the_legacy_profile(self) -> None:
        self._rewrite_marker(generated_at="", timestamp="not-a-timestamp")

        with self.assertRaisesRegex(DeploymentBindingError, "timestamp"):
            load_deployment_binding(self.model, "demo")

    def test_marker_sealed_before_the_cutoff_keeps_the_legacy_profile(self) -> None:
        self._rewrite_marker(generated_at="2026-08-31T23:59:59+00:00")

        binding = load_deployment_binding(self.model, "demo")

        self.assertEqual(binding["evidence"]["integrity_profile"], "legacy-partial-v1")

    def test_a_legacy_marker_does_not_need_a_finalized_portable_manifest(self) -> None:
        # Bundles sealed before the cutoff wrote artifact_manifest.json by hand: status is whatever
        # the author typed and model_dir an absolute path. Eval bound them on the marker hashes alone
        # and the legacy profile keeps that promise; only a declared profile buys the manifest check.
        write_json(self.artifacts / "artifact_manifest.json", {"status": "passed", "model_dir": "/nfs/models/demo"})

        binding = load_deployment_binding(self.model, "demo")

        self.assertEqual(binding["evidence"]["integrity_profile"], "legacy-partial-v1")

    def test_a_declared_profile_still_demands_a_finalized_portable_manifest(self) -> None:
        self._rewrite_marker(integrity_profile="manifest-complete-v1")
        manifest = json.loads((self.artifacts / "artifact_manifest.json").read_text())
        manifest["status"] = "passed"
        write_json(self.artifacts / "artifact_manifest.json", manifest)

        with self.assertRaisesRegex(DeploymentBindingError, "must be finalized"):
            load_deployment_binding(self.model, "demo")

    def test_marker_without_any_timestamp_may_not_fall_back_to_the_legacy_profile(self) -> None:
        marker = json.loads((self.artifacts / "deployment_ready.json").read_text())
        marker.pop("generated_at", None)
        write_json(self.artifacts / "deployment_ready.json", marker)

        with self.assertRaisesRegex(DeploymentBindingError, "integrity_profile"):
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



class MandatoryIntegrityPathTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.model = Path(tmp.name) / "model"
        artifacts = self.model / "artifacts"
        for name in (
            "artifact_manifest.json",
            "fixture_manifest.json",
            "package_gate.json",
            "runtime_inventory.json",
            "sample_output.json",
            "verdict.json",
            "model_runtime_manifest.json",
        ):
            write_json(artifacts / name, {})
        write_json(artifacts / "weights_manifest.json", {"required": True, "local_dir_name": "checkpoints"})
        gt = self.model / "fixture" / "asr" / "gt.jsonl"
        gt.parent.mkdir(parents=True, exist_ok=True)
        gt.write_text("{}\n", encoding="utf-8")

    def test_external_weights_are_not_required_inside_the_bundle(self) -> None:
        required = _mandatory_integrity_paths(self.model, {}, {}, "none", "external")

        self.assertNotIn("checkpoints", {Path(path).parts[0] for path in required})
        self.assertIn("artifacts/weights_manifest.json", required)
        self.assertIn("fixture/asr/gt.jsonl", required)

    def test_bundled_weights_must_be_present_inside_the_bundle(self) -> None:
        with self.assertRaisesRegex(DeploymentBindingError, "weights root is missing"):
            _mandatory_integrity_paths(self.model, {}, {}, "none", "bundled")

    def test_absent_weights_integrity_means_bundled(self) -> None:
        with self.assertRaisesRegex(DeploymentBindingError, "weights root is missing"):
            _mandatory_integrity_paths(self.model, {}, {}, "none", None)

    def test_unknown_weights_integrity_is_rejected(self) -> None:
        with self.assertRaisesRegex(DeploymentBindingError, "weights_integrity"):
            _mandatory_integrity_paths(self.model, {}, {}, "none", "extern")

    def _seal_complete_manifest(self, **marker_fields: object) -> dict:
        paths = sorted(
            {
                *CORE_BUNDLE_FILES,
                *COMMON_MANDATORY_SIDECARS,
                "artifacts/model_runtime_manifest.json",
                "artifacts/weights_manifest.json",
                "fixture/asr/gt.jsonl",
            }
        )
        write_json(
            self.model / "artifacts" / "artifact_manifest.json",
            {
                "schema": "sure.onboard.artifact_manifest.v1",
                "status": "finalized",
                "model_dir": ".",
                "artifacts": {
                    "required": {
                        path: {"path": path}
                        for path in [*paths, "artifacts/deployment_ready.json"]
                    }
                },
            },
        )
        return {"required_artifact_sha256": {path: "a" * 64 for path in paths}, **marker_fields}

    def test_external_weights_declared_on_the_marker_reach_the_mandatory_paths(self) -> None:
        marker = self._seal_complete_manifest(weights_integrity="external")

        manifest = _validate_complete_manifest(self.model, marker, {}, "none")

        self.assertEqual(manifest["status"], "finalized")

    def test_a_marker_without_weights_integrity_still_demands_bundled_weights(self) -> None:
        marker = self._seal_complete_manifest()

        with self.assertRaisesRegex(DeploymentBindingError, "weights root is missing"):
            _validate_complete_manifest(self.model, marker, {}, "none")


class IntegrityProfileCutoffTests(unittest.TestCase):
    def test_a_marker_sealed_before_the_cutoff_may_omit_the_integrity_profile(self) -> None:
        # The accepting branch of the cutoff: the bundle fixture cannot reach it
        # through load_deployment_binding on a machine without POSIX mount paths.
        _require_declared_integrity_profile({"generated_at": "2026-08-31T23:59:59Z"})


if __name__ == "__main__":
    unittest.main()
