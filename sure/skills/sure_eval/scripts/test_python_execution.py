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

import python_execution
import run_local_execution


class PythonExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        self.model_file = self.model_dir / "model.py"
        self.model_file.write_text("VALUE = 1\n", encoding="utf-8")
        self.entrypoint = self.root / "run.sh"
        self.entrypoint.write_text(
            "#!/bin/bash\n"
            "set -eu\n"
            "test -x \"$MODEL_PYTHON\"\n"
            "test -n \"$SURE_EVAL_MODEL_RUNTIME_ID\"\n"
            "test -z \"${OPENAI_API_KEY:-}\"\n"
            "test -z \"${SSH_AUTH_SOCK:-}\"\n",
            encoding="utf-8",
        )
        self.entrypoint.chmod(0o755)
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
                "OPENAI_API_KEY": "declared-secret",
                "SURE_EVAL_API_TOKEN": "also-secret",
            },
        }
        repo_root = Path(__file__).resolve().parents[4]
        with (
            patch.object(python_execution, "harness_runtime_from_eval_input", return_value=harness),
            patch.object(python_execution, "evaluation_runtime_from_eval_input", return_value=None),
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

        self.assertEqual(command, ["bash", str(self.entrypoint)])
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("SURE_EVAL_API_TOKEN", environment)
        self.assertNotIn("SSH_AUTH_SOCK", environment)
        self.assertEqual(environment["MODEL_PYTHON"], sys.executable)
        self.assertEqual(environment["HARNESS_PYTHON_BIN"], str(self.harness_python))
        self.assertNotEqual(environment["MODEL_PYTHON"], environment["HARNESS_PYTHON_BIN"])
        self.assertEqual(provenance["runtime_kind"], "python")
        completed = subprocess.run(command, env=environment, check=False)
        self.assertEqual(completed.returncode, 0)

    def test_model_integrity_is_checked_again_after_execution(self) -> None:
        python_execution.verify_model_integrity(self.binding)
        self.model_file.write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed after onboarding"):
            python_execution.verify_model_integrity(self.binding)

    def test_local_python_route_never_builds_a_container_command(self) -> None:
        artifacts = self.run_dir / "artifacts"
        artifacts.mkdir(parents=True)
        surface = {
            "entrypoint_path": str(self.entrypoint),
            "execution": {"requested": "local", "planned": "local", "path_planned": "local_python"},
        }
        (artifacts / "execution_surface.json").write_text(json.dumps(surface), encoding="utf-8")
        (artifacts / "eval_input_resolved.json").write_text(
            json.dumps({**self.eval_input, "runtime": {"run_dir": str(self.run_dir), "model_runtime": "python"}}),
            encoding="utf-8",
        )
        launch = {
            "runtime_kind": "python",
            "model_runtime": {"runtime_id": self.binding["python"]["runtime_id"]},
            "harness_runtime": {},
        }
        argv = ["run_local_execution.py", "--run-dir", str(self.run_dir)]
        with (
            patch.object(run_local_execution, "_vc_available", return_value=False),
            patch.object(
                run_local_execution,
                "build_local_python_command",
                return_value=(["bash", "-c", "exit 0"], os.environ.copy(), launch),
            ),
            patch.object(
                run_local_execution,
                "build_local_container_command",
                side_effect=AssertionError("Docker route must not be used"),
            ),
            patch.object(run_local_execution, "verify_model_integrity", return_value={}),
            patch.object(sys, "argv", argv),
        ):
            self.assertEqual(run_local_execution.main(), 0)
        submit = json.loads((artifacts / "submit_result.json").read_text(encoding="utf-8"))
        self.assertEqual(submit["execution_path"], "local_python")
        self.assertEqual(submit["runtime_kind"], "python")


if __name__ == "__main__":
    unittest.main()
