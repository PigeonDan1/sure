#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deployment_binding import DeploymentBindingError, load_deployment_binding

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from sure.runtime.model.bootstrap import materialize_runtime


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PythonDeploymentBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model = self.root / "models" / "demo"
        self.artifacts = self.model / "artifacts"
        self.artifacts.mkdir(parents=True)
        (self.model / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.model / "server.py").write_text("print('server')\n", encoding="utf-8")
        (self.model / "config.yaml").write_text("tools:\n  - name: predict\n", encoding="utf-8")
        self.lock = self.model / "requirements.lock"
        self.lock.write_text("", encoding="utf-8")
        self.runtime_base = self.root / "site-runtime" / "models"
        contract = materialize_runtime(
            runtime_root=self.runtime_base,
            source_python=Path(sys.executable),
            lock_path=self.lock,
        )
        self.runtime_dir = Path(contract["runtime_root"])
        manifest = {
            key: value
            for key, value in contract.items()
            if key
            not in {
                "runtime_root",
                "manifest_path",
                "python_executable_resolved",
                "manifest_sha256",
                "probe",
            }
        }
        write_json(self.artifacts / "model_runtime_manifest.json", manifest)
        core_hashes = {
            relative: sha256(self.model / relative)
            for relative in ("model.py", "server.py", "config.yaml", "requirements.lock")
        }
        inventory = {
            "schema": "sure.onboard.runtime_inventory.v2",
            "status": "ready",
            "model": {"name": "demo", "deployment_type": "local", "bundle_root": "."},
            "model_runtime": {
                "required": True,
                "runtime_type": "model_python",
                "backend": "uv",
                "runtime_id": manifest["runtime_id"],
                "python_executable": "bin/python",
                "python_version": manifest["python_version"],
                "python_abi": manifest["python_abi"],
                "manifest_path": "artifacts/model_runtime_manifest.json",
                "manifest_sha256": contract["manifest_sha256"],
                "lockfile_path": "requirements.lock",
                "lock_sha256": manifest["lock_sha256"],
                "working_dir": ".",
                "server_command": ["bin/python", "server.py"],
                "tool_names": ["predict"],
                "required_imports": [],
                "gpu_required": False,
            },
            "container_runtime": {"required": False},
            "policy": {
                "eval_runtime": "python",
                "host_python_fallback": False,
                "image_override_allowed": False,
                "nfs_models_mutable_by_eval": False,
            },
            "evidence": {"model_core_sha256": core_hashes},
        }
        package = {
            "schema": "sure.onboard.package_gate.v2",
            "status": "passed",
            "package_profile": "none",
            "readiness": {
                "local_ready": True,
                "container_ready": False,
                "docker_ready": False,
                "registry_ready": False,
                "bundle_ready": True,
            },
        }
        write_json(self.artifacts / "runtime_inventory.json", inventory)
        write_json(self.artifacts / "package_gate.json", package)
        hashes = {
            f"artifacts/{name}": sha256(self.artifacts / name)
            for name in ("runtime_inventory.json", "package_gate.json", "model_runtime_manifest.json")
        }
        marker = {
            "schema": "sure.onboard.deployment_ready.v2",
            "status": "ready",
            "model_name": "demo",
            "package_profile": "none",
            "model_runtime": inventory["model_runtime"],
            "required_artifact_sha256": hashes,
            "bundle_identity_sha256": hashlib.sha256(
                json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "execution_policy": {
                "container_only": False,
                "eval_runtime": "python",
                "isolation": "trusted_host",
                "model_integrity": "verify_before_after",
                "nfs_models_read_only": False,
                "model_bundle_mutation_allowed": False,
                "host_python_fallback": False,
                "approved_image_override": False,
            },
        }
        write_json(self.artifacts / "deployment_ready.json", marker)
        self.site_policy = self.root / "site.yaml"
        self.site_policy.write_text(
            "\n".join(
                [
                    "schema: sure.site.policy.v1",
                    "site_id: test",
                    "policy_version: 1",
                    "storage:",
                    f"  approved_models_roots: [{self.root / 'models'}]",
                    f"  approved_results_roots: [{self.root / 'results'}]",
                    f"  forbidden_output_roots: [{self.root / 'forbidden'}]",
                    f"  runtime_root: {self.root / 'site-runtime'}",
                    "datasets:",
                    f"  allowed_source_roots: [{self.root / 'datasets'}]",
                    "execution:",
                    "  surfaces: [local]",
                    "  local_runtimes: [python]",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def load(self) -> dict:
        with patch.dict(os.environ, {"SURE_SITE_POLICY": str(self.site_policy)}):
            return load_deployment_binding(self.model, "demo")

    def test_resolves_portable_runtime_id_against_active_site(self) -> None:
        binding = self.load()
        self.assertEqual(binding["schema"], "sure.eval.deployment_binding.v2")
        self.assertEqual(binding["runtime_kind"], "python")
        self.assertEqual(binding["python"]["runtime_id"], self.runtime_dir.name)
        self.assertEqual(
            binding["python"]["python_executable"],
            str(self.runtime_dir / "bin" / "python"),
        )
        self.assertNotIn(str(self.runtime_dir), (self.artifacts / "model_runtime_manifest.json").read_text())

    def test_rejects_model_code_changed_after_promotion(self) -> None:
        (self.model / "model.py").write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(DeploymentBindingError, "model integrity hash mismatch"):
            self.load()

    def test_rejects_missing_active_site_runtime(self) -> None:
        self.runtime_dir.rename(self.runtime_dir.with_name(self.runtime_dir.name + "-missing"))
        with self.assertRaises(DeploymentBindingError):
            self.load()

    def test_rejects_python_payload_labeled_as_legacy_v1(self) -> None:
        marker_path = self.artifacts / "deployment_ready.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["schema"] = "sure.onboard.deployment_ready.v1"
        write_json(marker_path, marker)

        with self.assertRaisesRegex(DeploymentBindingError, "Python deployment_ready schema"):
            self.load()


if __name__ == "__main__":
    unittest.main()
