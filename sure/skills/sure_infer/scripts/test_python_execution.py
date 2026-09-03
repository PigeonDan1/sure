#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import python_execution


class PythonExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        self.model_file = self.model_dir / "model.py"
        self.model_file.write_text("VALUE = 1\n", encoding="utf-8")
        self.entrypoint = self.root / "infer_entrypoint.py"
        self.entrypoint.write_text(
            "import os\n"
            "assert os.access(os.environ['MODEL_PYTHON'], os.X_OK)\n"
            "assert os.environ['SURE_EVAL_MODEL_RUNTIME_ID']\n"
            "assert 'OPENAI_API_KEY' not in os.environ\n"
            "assert 'SSH_AUTH_SOCK' not in os.environ\n"
            "assert os.environ['NO_RESUME'] == '1'\n"
            "assert os.environ['PROTOCOL_ID'] == 'strict_core'\n"
            "assert os.environ['PATH'] != '/tmp/agent/bin'\n",
            encoding="utf-8",
        )
        self.harness_python = self.root / "harness" / "bin" / "python"
        self.harness_python.parent.mkdir(parents=True)
        self.harness_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.harness_python.chmod(0o755)
        self.run_dir = self.root / "run"
        self.binding = {
            "schema": "sure.eval.deployment_binding.v2",
            "runtime_kind": "python",
            "model_dir": str(self.model_dir),
            "python": {
                "runtime_id": "sure-model-python-v1-" + "a" * 24,
                "python_executable": sys.executable,
                "working_dir": str(self.model_dir),
            },
            "policy": {
                "execution_mode": "python",
                "host_python_fallback": False,
            },
            "evidence": {
                "model_core_sha256": {
                    "model.py": python_execution._sha256(self.model_file),
                }
            },
        }
        self.eval_input = {
            "model": {"deployment_binding": self.binding},
            "runtime": {"run_dir": str(self.run_dir)},
        }

    def test_builds_sanitized_environment_for_approved_python(self) -> None:
        harness = {
            "runtime_id": "sure-harness-test",
            "python_executable": str(self.harness_python),
            "lock_sha256": "b" * 64,
            "manifest_path": str(self.root / "harness" / "runtime-manifest.json"),
            "runtime_root": str(self.root / "harness"),
        }
        surface = {
            "generation_method": "harness_template",
            "source_provenance": {},
            "env": {
                "TOOL_NAME": "predict",
                "NO_RESUME": "1",
                "PROTOCOL_ID": "strict_core",
                "PATH": "/tmp/agent/bin",
                "PYTHONPATH": "/tmp/agent",
                "OPENAI_API_KEY": "declared-secret",
                "SURE_EVAL_API_TOKEN": "also-secret",
            },
        }
        repo_root = Path(__file__).resolve().parents[4]
        with (
            patch.object(python_execution, "harness_runtime_from_eval_input", return_value=harness),
            patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "host-secret", "SSH_AUTH_SOCK": "/tmp/agent.sock"},
                clear=False,
            ),
        ):
            command, environment, provenance = python_execution.build_local_python_command(
                surface=surface,
                eval_input=self.eval_input,
                entrypoint=self.entrypoint,
                repo_root=repo_root,
            )

        self.assertEqual(command, [str(self.harness_python), str(self.entrypoint)])
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("SURE_EVAL_API_TOKEN", environment)
        self.assertNotIn("SSH_AUTH_SOCK", environment)
        self.assertEqual(environment["NO_RESUME"], "1")
        self.assertEqual(environment["PROTOCOL_ID"], "strict_core")
        self.assertNotEqual(environment["PATH"], "/tmp/agent/bin")
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual(environment["MODEL_PYTHON"], sys.executable)
        self.assertEqual(environment["HARNESS_PYTHON_BIN"], str(self.harness_python))
        self.assertNotEqual(environment["MODEL_PYTHON"], environment["HARNESS_PYTHON_BIN"])
        self.assertEqual(provenance["runtime_kind"], "python")
        self.assertNotIn("evaluation_runtime", provenance)

    def test_model_integrity_is_checked_again_after_execution(self) -> None:
        python_execution.verify_model_integrity(self.binding)
        self.model_file.write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed after onboarding"):
            python_execution.verify_model_integrity(self.binding)


if __name__ == "__main__":
    unittest.main()
