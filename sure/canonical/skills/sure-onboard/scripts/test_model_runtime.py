from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from materialize_model_runtime import materialize


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ModelRuntimeOnboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_dir = self.root / ".sure" / "runs" / "test"
        self.artifacts = self.run_dir / "artifacts"
        self.model_dir = self.root / "sure" / "models" / "demo"
        (self.model_dir / ".venv" / "bin").mkdir(parents=True)
        (self.model_dir / ".venv" / "bin" / "python").symlink_to(sys.executable)
        (self.model_dir / "requirements.lock").write_text("", encoding="utf-8")
        self.artifacts.mkdir(parents=True)
        self.policy = self.root / "config" / "site.local.yaml"
        self.policy.parent.mkdir(parents=True)
        self.policy.write_text(
            "\n".join(
                [
                    "schema: sure.site.policy.v1",
                    "site_id: test",
                    "policy_version: 1",
                    "storage:",
                    f"  approved_models_roots: [{self.root / 'approved-models'}]",
                    f"  approved_results_roots: [{self.root / 'approved-results'}]",
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
        write_json(
            self.artifacts / "model_input_resolved.json",
            {
                "model_id": "demo/model",
                "model_name": "demo",
                "model_dir": str(self.model_dir),
                "deployment_type": "local",
                "package_profile": "none",
                "device": "cpu",
            },
        )
        self.draft = self.artifacts / "build_env_draft.json"
        write_json(
            self.draft,
            {
                "env_ready": True,
                "backend": "uv",
                "model_dir": str(self.model_dir),
                "python_executable": ".venv/bin/python",
                "lockfile_path": "requirements.lock",
                "runtime_checks": {"required_imports": []},
            },
        )

    def test_materializer_emits_gate_ready_runtime_binding(self) -> None:
        output = self.artifacts / "build_env_result.json"
        result = materialize(self.run_dir, self.draft, output)

        self.assertTrue(result["model_runtime"]["runtime_id"].startswith("sure-model-python-v1-"))
        self.assertEqual(result["lockfile_path"], "requirements.lock")
        self.assertNotIn(str(self.root), (self.artifacts / "model_runtime_manifest.json").read_text())
        environment = os.environ.copy()
        environment["SURE_SITE_POLICY"] = str(self.policy)
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("check_env.py")),
                "--run-dir",
                str(self.run_dir),
                "--produces",
                str(output),
            ],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
