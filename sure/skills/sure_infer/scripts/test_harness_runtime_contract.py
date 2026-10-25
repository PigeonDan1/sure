#!/usr/bin/env python3
"""Cross-artifact integration test: one Harness Runtime identity from resolve to protocol.

The PR review demanded a test that drives ``resolve_eval_input`` ->
``build_local_container_command`` (which writes ``execution_provenance.json``) ->
``protocol_writer`` and asserts every artifact records the same active runtime
ID / lock SHA-256 / Python path, with ``harness_runtime_source ==
active_injected_runtime`` and the image's legacy Harness Runtime never executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import protocol_writer  # noqa: E402
import resolve_eval_input  # noqa: E402
import yaml  # noqa: E402
from container_execution import build_local_container_command, container_path  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parents[3]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def container_side(path: Path | str) -> str:
    """Independently derive the container spelling of a host path."""
    text = str(path)
    if len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:].replace("\\", "/")
    return text


class HarnessRuntimeContractTests(unittest.TestCase):
    """resolve_eval_input -> execution provenance -> protocol.yaml share one runtime."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()

        self.repo = root / "repo"
        self.repo.mkdir()

        # Active Harness Runtime: materialized under the mounted repository, so
        # resolve_container_harness_runtime accepts it as the single source.
        self.harness_runtime = self.repo / "sure" / ".runtime" / "harness" / "sure-harness-test"
        self.harness_python = self.harness_runtime / "bin" / "python"
        self.harness_python.parent.mkdir(parents=True)
        self.harness_python.write_bytes(b"#!/bin/sh\nexit 0\n")
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
                "materialization": "uv_venv",
                "materialization_version": 4,
            },
        )

        # Approved model bundle sealed over a legacy image Harness Runtime.
        self.model = root / "models" / "demo"
        self.artifacts = self.model / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.image = "registry.example.com/sure/demo:v1"
        self.digest = "sha256:" + "a" * 64
        self.image_ref = "registry.example.com/sure/demo@" + self.digest
        (self.model / "config.yaml").write_text(
            "model:\n  name: demo\n  task: ASR\n", encoding="utf-8"
        )
        write_json(self.artifacts / "verdict.json", {"status": "success"})
        self._write_bundle()
        self._declare_image_harness_runtime()

        # Site policy with everything rooted in the temp dir.
        self.projection = root / "dataset-projection"
        self.output = root / "out"
        self.control = root / "control"
        self.control.mkdir()
        self.entrypoint = self.control / "run_infer.py"
        self.entrypoint.write_text("# placeholder\n", encoding="utf-8")
        self.site_policy = root / "site.yaml"
        self.site_policy.write_text(
            "schema: sure.site.policy.v1\n"
            "site_id: contract-test\n"
            "policy_version: 1\n"
            "storage:\n"
            "  approved_models_roots:\n"
            "    - " + str(root / "models").replace("\\", "/") + "\n"
            "  forbidden_output_roots:\n"
            "    - " + str(root / "forbidden").replace("\\", "/") + "\n"
            "  runtime_root: " + str(root / "runtime").replace("\\", "/") + "\n"
            "datasets:\n"
            "  allowed_source_roots:\n"
            "    default: " + str(root / "datasets").replace("\\", "/") + "\n"
            "  projection_root: " + str(self.projection).replace("\\", "/") + "\n"
            "execution:\n"
            "  surfaces: [local]\n"
            "  local_runtimes: [container]\n",
            encoding="utf-8",
        )
        jsonl_dir = self.projection / "sure_benchmark" / "jsonl"
        jsonl_dir.mkdir(parents=True)
        (jsonl_dir / "ds.jsonl").write_text(
            json.dumps({"task": "ASR", "language": "en", "metadata": {}}) + "\n",
            encoding="utf-8",
        )

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

    def _image_runtime_binding(self) -> dict:
        image_root = "/opt/sure-harness/sure-harness-test"
        return {
            **self._runtime_binding(),
            "python_executable": f"{image_root}/bin/python",
            "manifest_path": f"{image_root}/runtime-manifest.json",
            "runtime_root": image_root,
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
                    "gpu_required": False,
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
                "bundle_identity_sha256": hashlib.sha256(
                    json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "required_artifact_sha256": hashes,
                "execution_policy": {
                    "container_only": True,
                    "nfs_models_read_only": True,
                    "host_python_fallback": False,
                    "approved_image_override": False,
                },
            },
        )

    def _declare_image_harness_runtime(self) -> None:
        declared = {"required": True, **self._image_runtime_binding()}
        inventory = json.loads((self.artifacts / "runtime_inventory.json").read_text())
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

    def _approved_model_resolution(self) -> dict:
        from deployment_binding import load_deployment_binding

        return {
            "schema": "sure.eval.approved_model_resolution.v1",
            "ok": True,
            "model": "demo",
            "model_dir": str(self.model),
            "source": "approved_nfs_models",
            "approved_models_root": str(self.model.parent),
            "verdict_path": str(self.artifacts / "verdict.json"),
            "verdict_status": "success",
            "runtime_ready": True,
            "verdict_ready": True,
            "workflow_ready": True,
            "integration_state": "onboarded",
            "deployment_binding": load_deployment_binding(self.model, "demo"),
            "deployment_error": None,
            "identity_error": None,
            "checks": {"config_yaml": True, "deployment_ready_json": True},
        }

    def _args(self) -> argparse.Namespace:
        return argparse.Namespace(
            model="demo",
            datasets=["ds"],
            protocol="standard_system",
            device="cpu",
            metrics=[],
            max_samples=0,
            execution="auto",
            execution_path="auto",
            evaluation_backend="external",
            evaluation_engine_root=str(Path(self.temp.name) / "no-engine"),
            user_goal="evaluate_existing_model",
            run_id="contract-run",
            config=None,
            datasets_root=None,
            output=None,
            output_dir=str(self.output),
            dataset_source_key="",
        )

    def test_resolve_provenance_and_protocol_record_one_harness_runtime(self) -> None:
        with (
            mock.patch.dict(os.environ, {"SURE_SITE_POLICY": str(self.site_policy)}),
            mock.patch.object(
                resolve_eval_input,
                "load_harness_runtime",
                return_value=self._runtime_binding(),
            ),
            mock.patch.object(
                resolve_eval_input,
                "_resolve_model",
                return_value=self._approved_model_resolution(),
            ),
        ):
            payload = resolve_eval_input.build_payload(self._args())

        runtime = payload["runtime"]
        self.assertEqual(runtime["harness_runtime_source"], "active_injected_runtime")
        self.assertEqual(runtime["harness_runtime_id"], "sure-harness-test")
        self.assertEqual(runtime["harness_lock_sha256"], "c" * 64)
        self.assertEqual(runtime["harness_python_executable"], str(self.harness_python))

        eval_input_path = self.control / "eval_input_resolved.json"
        write_json(eval_input_path, payload)

        command, launch = build_local_container_command(
            surface={"env": {"TOOL_NAME": "transcribe_audio"}},
            eval_input=payload,
            control_run_dir=self.control,
            entrypoint=self.entrypoint,
            repo_root=self.repo,
            device_request="cpu",
        )

        # The image's legacy Harness Runtime is recorded as evidence on the
        # binding but never reaches the docker command.
        image_python = "/opt/sure-harness/sure-harness-test/bin/python"
        self.assertEqual(
            payload["model"]["deployment_binding"]["container"]["harness_runtime_evidence"][
                "participates_in_execution"
            ],
            False,
        )
        self.assertNotIn(image_python, " ".join(command))
        self.assertEqual(launch["harness_runtime"]["execution_source"], "active_injected_runtime")
        self.assertEqual(launch["harness_runtime"]["python_executable"], str(self.harness_python))

        provenance = launch["execution_provenance"]
        self.assertEqual(provenance["harness_runtime_source"], "active_injected_runtime")
        self.assertEqual(provenance["harness_runtime_id"], "sure-harness-test")
        self.assertEqual(provenance["harness_lock_sha256"], "c" * 64)
        self.assertEqual(provenance["harness_python_executable"], str(self.harness_python))

        # The generation status records the same identity the entrypoint saw.
        run_dir = Path(payload["runtime"]["run_dir"])
        write_json(
            run_dir / "prediction_generation_status.json",
            {
                "schema": "sure.eval.prediction_generation_status.v2",
                "runtime": {
                    "harness_runtime": {
                        "schema": "sure.harness.runtime.binding.v1",
                        "runtime_id": "sure-harness-test",
                        "runtime_type": "harness_python",
                        "python_executable": str(self.harness_python),
                        "process_python_executable": sys.executable,
                        "lock_sha256": "c" * 64,
                        "manifest_path": str(self.harness_manifest),
                        "runtime_root": str(self.harness_runtime),
                        "execution_source": "active_injected_runtime",
                    },
                },
            },
        )
        protocol_writer.write_protocol_yaml(
            run_dir,
            "standard_system",
            self.model,
            results=[],
            tool_name="transcribe_audio",
        )
        protocol = yaml.safe_load((run_dir / "protocol.yaml").read_text(encoding="utf-8"))
        harness_block = protocol["inference_environment"]["harness_runtime"]
        self.assertEqual(harness_block["runtime_id"], "sure-harness-test")
        self.assertEqual(harness_block["lock_sha256"], "c" * 64)
        self.assertEqual(harness_block["python_executable"], str(self.harness_python))
        self.assertEqual(harness_block["execution_source"], "active_injected_runtime")

        # The image's Python never reaches the docker command and is not
        # recorded as an execution source in any run artifact. It may still
        # appear in eval_input_resolved.json under the deployment binding's
        # `harness_runtime_evidence` block, where it is explicitly marked
        # `participates_in_execution: false`.
        for artifact in (
            json.dumps(provenance, sort_keys=True),
            (run_dir / "protocol.yaml").read_text(encoding="utf-8"),
        ):
            self.assertNotIn(image_python, artifact)
        evidence = payload["model"]["deployment_binding"]["evidence"]
        self.assertEqual(evidence["harness_runtime_source"], "legacy_approval_evidence")
        self.assertFalse(evidence["harness_runtime_participates_in_execution"])

    def test_the_docker_entrypoint_is_the_active_runtime_s_own_python(self) -> None:
        with (
            mock.patch.dict(os.environ, {"SURE_SITE_POLICY": str(self.site_policy)}),
            mock.patch.object(
                resolve_eval_input,
                "load_harness_runtime",
                return_value=self._runtime_binding(),
            ),
            mock.patch.object(
                resolve_eval_input,
                "_resolve_model",
                return_value=self._approved_model_resolution(),
            ),
        ):
            payload = resolve_eval_input.build_payload(self._args())

        command, _ = build_local_container_command(
            surface={"env": {}},
            eval_input=payload,
            control_run_dir=self.control,
            entrypoint=self.entrypoint,
            repo_root=self.repo,
            device_request="cpu",
        )
        self.assertEqual(command[command.index("--entrypoint") + 1], container_side(self.harness_python.resolve()))
        self.assertIn(f"HARNESS_PYTHON_BIN={container_side(self.harness_python.resolve())}", command)


if __name__ == "__main__":
    unittest.main()
