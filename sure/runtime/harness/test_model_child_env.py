#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from model_child_env import model_child_env

# The filter keeps absolute paths only, and "/opt/..." is not absolute on
# Windows, so the model's own entry has to be spelled the way the host spells
# one or the test would assert against a path production rightly drops.
MODEL_PATH = "C:\\opt\\model-runtime" if os.name == "nt" else "/opt/model-runtime"


class ModelChildEnvTests(unittest.TestCase):
    def test_keeps_image_model_path_and_removes_harness_and_repo_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = root / "harness"
            repo = root / "repo"
            source = {
                "PYTHONHOME": str(harness / "base"),
                "PYTHONPATH": os.pathsep.join(
                    [str(harness / "site-packages"), str(repo / "sure/skills/sure_infer/scripts"), MODEL_PATH]
                ),
                "PYTHONEXECUTABLE": str(harness / "bin/python"),
                "SURE_HARNESS_RUNTIME_ROOT": str(harness),
                "SURE_EVAL_CONTAINER_REPO_ROOT": str(repo),
            }

            environment = model_child_env(source)

            self.assertEqual(environment["PYTHONPATH"], MODEL_PATH)
            self.assertNotIn("PYTHONHOME", environment)
            self.assertNotIn("PYTHONEXECUTABLE", environment)

    def test_removes_python_path_without_explicit_isolation_roots(self) -> None:
        environment = model_child_env({"PYTHONPATH": MODEL_PATH})
        self.assertNotIn("PYTHONPATH", environment)

    def test_drops_relative_python_path_entries(self) -> None:
        environment = model_child_env(
            {
                "PYTHONPATH": os.pathsep.join([".", "relative", MODEL_PATH]),
                "SURE_HARNESS_RUNTIME_ROOT": str(Path(tempfile.gettempdir()) / "sure-harness"),
                "SURE_EVAL_CONTAINER_REPO_ROOT": str(Path(tempfile.gettempdir()) / "workspace-sure"),
            }
        )
        self.assertEqual(environment["PYTHONPATH"], MODEL_PATH)


if __name__ == "__main__":
    unittest.main()
