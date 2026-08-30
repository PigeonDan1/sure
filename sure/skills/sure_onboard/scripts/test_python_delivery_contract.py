#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stage_model_artifacts
from finalize_model_bundle import finalize
from materialize_model_runtime import materialize
from write_runtime_inventory import write_inventory


SCRIPT_DIR = Path(__file__).resolve().parent


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class PythonDeliveryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_dir = self.root / ".sure" / "runs" / "python-ready"
        self.run_artifacts = self.run_dir / "artifacts"
        self.model_dir = self.root / "sure" / "models" / "demo"
        self.model_artifacts = self.model_dir / "artifacts"
        self.run_artifacts.mkdir(parents=True)
        self.model_dir.mkdir(parents=True)
        for name in stage_model_artifacts.CORE_FILES:
            (self.model_dir / name).write_text("# test\n", encoding="utf-8")
        (self.model_dir / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.model_dir / "server.py").write_text("print('server')\n", encoding="utf-8")
        (self.model_dir / "config.yaml").write_text(
            "task: asr\n"
            "server:\n  command: [.venv/bin/python, server.py]\n  working_dir: .\n"
            "tools:\n  - name: predict\n"
            "resources:\n  gpu: false\n",
            encoding="utf-8",
        )
        (self.model_dir / ".venv" / "bin").mkdir(parents=True)
        (self.model_dir / ".venv" / "bin" / "python").symlink_to(sys.executable)
        (self.model_dir / "requirements.lock").write_text("", encoding="utf-8")
        self.site_policy = self.root / "config" / "site.local.yaml"
        self.site_policy.parent.mkdir(parents=True)
        self.site_policy.write_text(
            "\n".join(
                [
                    "schema: sure.site.policy.v1",
                    "site_id: test",
                    "policy_version: 1",
                    "storage:",
                    f"  approved_models_roots: [{self.root / 'sure' / 'models'}]",
                    f"  approved_results_roots: [{self.root / 'results'}]",
                    f"  forbidden_output_roots: [{self.root / 'forbidden'}]",
                    f"  runtime_root: {self.root / 'runtime'}",
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
        self.environment = os.environ.copy()
        self.environment["SURE_SITE_POLICY"] = str(self.site_policy)
        self.resolved = {
            "model_id": "demo/model",
            "model_name": "demo",
            "model_dir": str(self.model_dir),
            "repo_url": "https://example.com/demo.git",
            "task_type": "asr",
            "deployment_type": "local",
            "package_profile": "none",
            "device": "cpu",
        }
        write_json(self.run_artifacts / "model_input_resolved.json", self.resolved)

    def run_gate(self, name: str, produces: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT_DIR / name), "--run-dir", str(self.run_dir), "--produces", str(produces)],
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_package_none_reaches_eval_ready_without_docker_evidence(self) -> None:
        draft = self.run_artifacts / "build_env_draft.json"
        write_json(
            draft,
            {
                "env_ready": True,
                "backend": "uv",
                "model_dir": str(self.model_dir),
                "python_executable": ".venv/bin/python",
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "lockfile_path": "requirements.lock",
                "runtime_checks": {"required_imports": []},
            },
        )
        with patch.dict(os.environ, {"SURE_SITE_POLICY": str(self.site_policy)}):
            materialize(self.run_dir, draft, self.run_artifacts / "build_env_result.json")

        passing_artifacts = {
            "context_selection.json": {"task_type": "asr", "selected_references": []},
            "repo_summary.json": {"repo_url": "https://example.com/demo.git"},
            "classification.json": {"task_type": "asr"},
            "backend_choice.json": {"backend": "uv"},
            "build_plan.json": {"model_id": "demo/model", "model_dir": str(self.model_dir), "backend": "uv", "steps": [], "package_profile": "none"},
            "spec_validation.json": {"checks": {}, "status": "passed"},
            "fixture_manifest.json": {"model_dir": str(self.model_dir), "task_type": "asr", "staged_dir": str(self.root), "gt_jsonl": str(self.root / "gt.jsonl"), "samples": [], "sample_count": 0},
            "weights_manifest.json": {"weights_ready": True},
            "env_compat_result.json": {"compat_ok": True},
            "import_result.json": {"import_passed": True},
            "load_result.json": {"load_passed": True},
            "infer_result.json": {"infer_passed": True},
            "contract_result.json": {"contract_passed": True},
            "wrapper_manifest.json": {"wrapper_path": "model.py"},
            "sample_output.json": {"prediction": "ok"},
        }
        for name, value in passing_artifacts.items():
            write_json(self.run_artifacts / name, value)

        stage_result = stage_model_artifacts.main_with_args(
            ["--run-dir", str(self.run_dir), "--produces", str(self.run_artifacts / "artifact_manifest.json")]
        )
        self.assertEqual(stage_result, 0)
        package = {
            "schema": "sure.onboard.package_gate.v2",
            "status": "passed",
            "package_profile": "none",
            "model_name": "demo",
            "model_dir": str(self.model_dir),
            "artifact_manifest_path": str(self.run_artifacts / "artifact_manifest.json"),
            "readiness": {
                "local_ready": True,
                "container_ready": False,
                "docker_ready": False,
                "registry_ready": False,
                "bundle_ready": True,
            },
            "local": {"validation_passed": True, "artifacts_complete": True},
        }
        write_json(self.run_artifacts / "package_gate.json", package)
        package_check = self.run_gate("check_package_gate.py", self.run_artifacts / "package_gate.json")
        self.assertEqual(package_check.returncode, 0, package_check.stderr)

        with patch.dict(os.environ, {"SURE_SITE_POLICY": str(self.site_policy)}):
            inventory = write_inventory(
                self.model_dir,
                self.run_artifacts / "runtime_inventory.json",
                self.run_dir,
            )
        self.assertEqual(inventory["status"], "ready")
        self.assertEqual(inventory["policy"]["eval_runtime"], "python")
        self.assertFalse(inventory["container_runtime"]["required"])
        inventory_check = self.run_gate("check_runtime_inventory.py", self.run_artifacts / "runtime_inventory.json")
        self.assertEqual(inventory_check.returncode, 0, inventory_check.stderr)

        verdict = {
            "status": "passed",
            "package_profile": "none",
            "build": {"success": True},
            "validation": {
                name: {"passed": True}
                for name in ("import_test", "load_test", "infer_test", "contract_test")
            },
            "readiness": package["readiness"],
        }
        write_json(self.run_artifacts / "verdict.json", verdict)
        verdict_check = self.run_gate("check_verdict.py", self.run_artifacts / "verdict.json")
        self.assertEqual(verdict_check.returncode, 0, verdict_check.stderr)

        with patch.dict(os.environ, {"SURE_SITE_POLICY": str(self.site_policy)}):
            deployment = finalize(self.run_dir, self.run_artifacts / "deployment_ready.json")
        self.assertEqual(deployment["status"], "ready")
        self.assertEqual(deployment["schema"], "sure.onboard.deployment_ready.v2")
        self.assertEqual(deployment["execution_policy"]["eval_runtime"], "python")
        self.assertEqual(deployment["execution_policy"]["isolation"], "trusted_host")
        final_check = self.run_gate("check_finalized_bundle.py", self.run_artifacts / "deployment_ready.json")
        self.assertEqual(final_check.returncode, 0, final_check.stderr)
        self.assertFalse(any(self.model_artifacts.glob("docker_*.json")))


if __name__ == "__main__":
    unittest.main()
