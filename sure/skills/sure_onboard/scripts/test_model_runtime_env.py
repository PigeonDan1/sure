#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime" / "harness"))
from model_child_env import model_child_env


class ModelRuntimeEnvTests(unittest.TestCase):
    def test_removes_harness_interpreter_state_only(self) -> None:
        env = model_child_env(
            {
                "PYTHONHOME": "/opt/sure-harness/base",
                "PYTHONPATH": "/opt/sure-harness/site-packages",
                "SURE_HARNESS_RUNTIME_ROOT": "/opt/sure-harness",
                "LD_LIBRARY_PATH": "/opt/sure-harness/base/lib:/usr/local/lib",
                "KEEP_ME": "yes",
            }
        )
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/local/lib")
        self.assertEqual(env["KEEP_ME"], "yes")

    def test_child_python_starts_after_invalid_pythonhome_is_removed(self) -> None:
        env = model_child_env({"PYTHONHOME": "/definitely/not/a/python", "PATH": "/usr/bin:/bin"})
        completed = subprocess.run(
            [sys.executable, "-c", "import encodings; print('ok')"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
