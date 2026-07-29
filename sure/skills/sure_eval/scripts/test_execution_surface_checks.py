#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_execution_surface_compliance as checks  # noqa: E402


class FakeCompleted:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


class InferenceRuntimeCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, checks.subprocess, "run", checks.subprocess.run)
        self.addCleanup(setattr, checks.shutil, "which", checks.shutil.which)
        self.torch_ok = True
        checks.subprocess.run = lambda *a, **k: FakeCompleted(
            0 if self.torch_ok else 1, "" if self.torch_ok else "ModuleNotFoundError: No module named 'torch'"
        )
        checks.shutil.which = lambda name: sys.executable if name == "python3" else None

    def write_surface(self, **env_overrides: object) -> Path:
        env = {
            "MODEL_PYTHON": sys.executable,
            "PYTHON_BIN": sys.executable,
            "HARNESS_PYTHON_BIN": "python3",
            "TOOL_NAME": "transcribe_audio",
        }
        env.update({k: v for k, v in env_overrides.items() if v is not None})
        for key, value in env_overrides.items():
            if value is None:
                env.pop(key, None)
        surface = {
            "execution": {"requested": "local", "path_planned": "local_bash"},
            "env": env,
            "resolved_inputs": {"model_dir": str(Path(self.tmp.name) / "model"), "tool_name": env.get("TOOL_NAME", "")},
        }
        path = Path(self.tmp.name) / "execution_surface.json"
        path.write_text(json.dumps(surface), encoding="utf-8")
        return path

    def test_harness_interpreter_may_be_a_bare_command_on_path(self) -> None:
        result = checks.check_inference_runtime(self.write_surface())
        self.assertTrue(result["passed"], result.get("evidence"))

    def test_harness_interpreter_missing_from_path_is_reported(self) -> None:
        result = checks.check_inference_runtime(self.write_surface(HARNESS_PYTHON_BIN="python9"))
        self.assertFalse(result["passed"])
        self.assertIn("python9", result["evidence"])

    def test_model_interpreter_must_be_absolute(self) -> None:
        result = checks.check_inference_runtime(self.write_surface(MODEL_PYTHON=".venv/bin/python"))
        self.assertFalse(result["passed"])
        self.assertIn("must be an absolute path", result["evidence"])

    def test_torch_probe_still_runs_when_another_interpreter_is_broken(self) -> None:
        self.torch_ok = False
        result = checks.check_inference_runtime(self.write_surface(HARNESS_PYTHON_BIN="python9"))
        self.assertFalse(result["passed"])
        self.assertIn("python9", result["evidence"])
        self.assertIn("cannot import torch", result["evidence"])

    def test_missing_model_interpreter_is_reported(self) -> None:
        result = checks.check_inference_runtime(self.write_surface(MODEL_PYTHON=None, PYTHON_BIN=None))
        self.assertFalse(result["passed"])
        self.assertIn("MODEL_PYTHON", result["evidence"])

    def test_vc_execution_skips_local_runtime_checks(self) -> None:
        path = Path(self.tmp.name) / "execution_surface.json"
        path.write_text(
            json.dumps(
                {
                    "execution": {"requested": "vc", "path_planned": "vc_submit"},
                    "env": {"MODEL_PYTHON": "relative/python"},
                    "resolved_inputs": {},
                }
            ),
            encoding="utf-8",
        )
        result = checks.check_inference_runtime(path)
        self.assertTrue(result["passed"])

    def test_tool_name_must_be_declared_by_the_model(self) -> None:
        model_dir = Path(self.tmp.name) / "model"
        model_dir.mkdir(exist_ok=True)
        (model_dir / "config.yaml").write_text("tools:\n  - name: transcribe_audio\n", encoding="utf-8")
        result = checks.check_inference_runtime(self.write_surface(TOOL_NAME="transcribe"))
        self.assertFalse(result["passed"])
        self.assertIn("transcribe_audio", result["evidence"])


if __name__ == "__main__":
    unittest.main()
