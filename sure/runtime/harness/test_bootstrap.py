#!/usr/bin/env python3
"""Tests for the Harness Runtime bootstrap."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "runtime" / "harness" / "bootstrap.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.runtime.harness.bootstrap import _load_spec, resolve_runtime
from sure.runtime.uvenv import runtime_python_relative


class HarnessRuntimeBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.runtime_root = Path(self._temporary.name) / "harness"

    def test_the_runtime_id_changes_with_the_materialization_version(self) -> None:
        # The id used to carry only the harness version, the ABI and the lock
        # hash, so a rebuilt runtime kept the same id while its manifest said
        # something else. Every gate compares the id by string equality.
        spec, _lock_path, lock_sha256, runtime_id = _load_spec()
        self.assertIn(f"-m{spec['materialization_version']}-", runtime_id)
        self.assertTrue(runtime_id.startswith("sure-harness-"))
        self.assertTrue(runtime_id.endswith(lock_sha256[:12]))

    def test_materializing_twice_yields_one_verified_runtime(self) -> None:
        first = resolve_runtime(self.runtime_root)
        second = resolve_runtime(self.runtime_root)

        self.assertEqual(first["runtime_id"], second["runtime_id"])
        self.assertEqual(first["lock_sha256"], second["lock_sha256"])
        self.assertEqual(first["status"], "ready")
        python = Path(first["python_executable"])
        self.assertTrue(python.is_file())
        # "Scripts/python.exe" on Windows, "bin/python" everywhere else.
        expected_directory, expected_name = runtime_python_relative().split("/")
        self.assertEqual(python.name, expected_name)
        self.assertEqual(python.parent.name, expected_directory)
        manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["materialization"], "uv_venv")
        self.assertEqual(len(manifest["base_python_sha256"]), 64)
        self.assertEqual(manifest["runtime_id"], first["runtime_id"])


if __name__ == "__main__":
    unittest.main()
