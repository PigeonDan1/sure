from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent


class OnboardContainerDeliveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.run_dir = self.repo / ".sure" / "runs" / "test"
        self.input_path = self.root / "model_input.yaml"
        self.input_path.write_text(
            yaml.safe_dump(
                {
                    "model_id": "example/demo",
                    "model_name": "example__demo",
                    "task_type": "asr",
                    "deployment_type": "local",
                    "repo": {"url": "https://example.invalid/demo"},
                }
            ),
            encoding="utf-8",
        )
        policy = {
            "schema": "sure.site.policy.v1",
            "site_id": "test-site",
            "policy_version": 1,
            "storage": {
                "approved_models_roots": ["/srv/models"],
                "approved_results_roots": ["/srv/results"],
                "forbidden_output_roots": ["/srv"],
                "runtime_root": "/srv/runtime",
            },
            "datasets": {"allowed_source_roots": ["/srv/datasets"]},
            "execution": {"surfaces": ["local"]},
            "network": {"container_registry": "registry.example"},
            "container_delivery": {
                "repository_template": "{registry}/org/{task}-{model_name}"
            },
        }
        self.policy_path = self.root / "site.yaml"
        self.policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
        self.environment = {
            **os.environ,
            "SURE_SITE_POLICY": str(self.policy_path.resolve()),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(self, name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / name), *arguments],
            check=False,
            capture_output=True,
            text=True,
            env=self.environment,
        )

    def materialize(self) -> dict:
        result = self.run_script(
            "materialize_onboard_inputs.py",
            "--model-input-path",
            str(self.input_path),
            "--run-dir",
            str(self.run_dir),
            "--repo-root",
            str(self.repo),
            "--package-profile",
            "docker-registry",
            "--image-version",
            "0.1.7",
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(
            (self.run_dir / "artifacts" / "model_input_resolved.json").read_text(
                encoding="utf-8"
            )
        )

    def test_materializer_and_model_input_gate_use_site_resolved_target(self) -> None:
        resolved = self.materialize()
        expected = "registry.example/org/asr-example__demo:0.1.7"
        self.assertEqual(resolved["container_delivery"]["target_image"], expected)
        gate = self.run_script(
            "check_model_input.py",
            "--run-dir",
            str(self.run_dir),
            "--produces",
            str(self.run_dir / "artifacts" / "model_input_resolved.json"),
        )
        self.assertEqual(gate.returncode, 0, gate.stderr or gate.stdout)

    def test_build_plan_rejects_a_target_not_resolved_by_site_policy(self) -> None:
        resolved = self.materialize()
        target = resolved["container_delivery"]["target_image"]
        plan = {
            "model_id": "example/demo",
            "model_dir": resolved["model_dir"],
            "backend": "docker",
            "deployment_type": "local",
            "package_profile": "docker-registry",
            "container_delivery": {
                "dockerfile_path": "Dockerfile",
                "target_image": target,
                "registry_required": True,
                "model_mount_read_only": True,
                "result_mount_separate": True,
            },
            "steps": [
                {
                    "name": "Dockerfile adaptation",
                    "commands": [
                        "docker build -t image .",
                        "docker run --rm image validate",
                        "docker push image",
                        "docker pull image@sha256:digest",
                    ],
                }
            ],
            "blockers": [],
        }
        plan_path = self.run_dir / "artifacts" / "build_plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        accepted = self.run_script(
            "check_build_plan.py",
            "--run-dir",
            str(self.run_dir),
            "--produces",
            str(plan_path),
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr or accepted.stdout)

        plan["container_delivery"]["target_image"] = "registry.example/wrong/demo:0.1.7"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        rejected = self.run_script(
            "check_build_plan.py",
            "--run-dir",
            str(self.run_dir),
            "--produces",
            str(plan_path),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("must exactly match", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
