from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import describe_harness_runtime  # noqa: E402

RUNTIME_ID = "sure-harness-test"
LOCK_SHA256 = "e" * 64
IMAGE_REF = "registry.example/sure-harness@sha256:" + "f" * 64


class DescribeHarnessRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runtime_root = self.root / "harness" / RUNTIME_ID
        (self.runtime_root / "bin").mkdir(parents=True)
        (self.runtime_root / "runtime-manifest.json").write_text(
            json.dumps({
                "schema": "sure.harness.runtime.manifest.v1",
                "runtime_id": RUNTIME_ID,
                "lock_sha256": LOCK_SHA256,
            }) + "\n",
            encoding="utf-8",
        )
        self.base_python = self.root / "base" / "bin" / "python3.11"
        self.base_python.parent.mkdir(parents=True)
        self.base_python.write_text("", encoding="utf-8")

    def _describe(self, python: Path) -> dict:
        environment = {
            "SURE_HARNESS_RUNTIME_ID": RUNTIME_ID,
            "SURE_HARNESS_LOCK_SHA256": LOCK_SHA256,
            "SURE_HARNESS_MANIFEST_PATH": str(self.runtime_root / "runtime-manifest.json"),
            "SURE_HARNESS_RUNTIME_ROOT": str(self.runtime_root),
            "HARNESS_PYTHON_BIN": str(python),
            "SURE_HARNESS_RUNTIME_IMAGE": IMAGE_REF,
        }
        with mock.patch.dict(os.environ, environment):
            return describe_harness_runtime.describe()

    def test_a_venv_interpreter_that_is_a_symlink_is_inside_the_runtime(self) -> None:
        python = self.runtime_root / "bin" / "python"
        try:
            python.symlink_to(self.base_python)
        except OSError as error:
            self.skipTest(f"cannot create a symlink here: {error}")
        described = self._describe(python)
        self.assertEqual(described["build_context"]["source"], f"docker-image://{IMAGE_REF}")

    def test_an_interpreter_outside_the_runtime_root_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "runtime paths are inconsistent"):
            self._describe(self.base_python)


if __name__ == "__main__":
    unittest.main()
