#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from model_child_env import model_child_env


class ModelChildEnvTests(unittest.TestCase):
    def test_keeps_image_model_path_and_removes_harness_and_repo_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = root / "harness"
            repo = root / "repo"
            source = {
                "PYTHONHOME": str(harness / "base"),
                "PYTHONPATH": os.pathsep.join(
                    [str(harness / "site-packages"), str(repo / "sure/skills/sure_infer/scripts"), "/opt/model-runtime"]
                ),
                "PYTHONEXECUTABLE": str(harness / "bin/python"),
                "SURE_HARNESS_RUNTIME_ROOT": str(harness),
                "SURE_EVAL_CONTAINER_REPO_ROOT": str(repo),
            }

            environment = model_child_env(source)

            self.assertEqual(environment["PYTHONPATH"], "/opt/model-runtime")
            self.assertNotIn("PYTHONHOME", environment)
            self.assertNotIn("PYTHONEXECUTABLE", environment)

    def test_removes_python_path_without_explicit_isolation_roots(self) -> None:
        environment = model_child_env({"PYTHONPATH": "/opt/model-runtime"})
        self.assertNotIn("PYTHONPATH", environment)

    def test_drops_relative_python_path_entries(self) -> None:
        environment = model_child_env(
            {
                "PYTHONPATH": os.pathsep.join([".", "relative", "/opt/model-runtime"]),
                "SURE_HARNESS_RUNTIME_ROOT": "/opt/sure-harness/runtime",
                "SURE_EVAL_CONTAINER_REPO_ROOT": "/workspace/sure",
            }
        )
        self.assertEqual(environment["PYTHONPATH"], "/opt/model-runtime")


if __name__ == "__main__":
    unittest.main()
