#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_runtime import HarnessRuntimeBindingError, load_harness_runtime


class HarnessRuntimeBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "runtime"
        python = self.root / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
        self.manifest = self.root / "runtime-manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema": "sure.harness.runtime.manifest.v1",
                    "runtime_id": "sure-harness-v1-py311-demo",
                    "lock_sha256": "a" * 64,
                    "python_version": "3.11.5",
                    "python_abi": "cp311",
                    "harness_version": "v1",
                    "materialization": "portable_host_cpython",
                    "materialization_version": 2,
                }
            ),
            encoding="utf-8",
        )
        self.env = {
            "HARNESS_PYTHON_BIN": str(python),
            "SURE_HARNESS_RUNTIME_ID": "sure-harness-v1-py311-demo",
            "SURE_HARNESS_LOCK_SHA256": "a" * 64,
            "SURE_HARNESS_MANIFEST_PATH": str(self.manifest),
        }

    def test_loads_matching_runtime(self) -> None:
        binding = load_harness_runtime(self.env)
        self.assertEqual(binding["runtime_id"], self.env["SURE_HARNESS_RUNTIME_ID"])
        self.assertEqual(binding["python_abi"], "cp311")

    def test_rejects_lock_mismatch(self) -> None:
        self.env["SURE_HARNESS_LOCK_SHA256"] = "b" * 64
        with self.assertRaisesRegex(HarnessRuntimeBindingError, "disagrees"):
            load_harness_runtime(self.env)

    def test_rejects_python_outside_runtime(self) -> None:
        outside = Path(self.temp.name) / "python"
        outside.write_text("#!/bin/sh\n", encoding="utf-8")
        outside.chmod(0o755)
        self.env["HARNESS_PYTHON_BIN"] = str(outside)
        with self.assertRaisesRegex(HarnessRuntimeBindingError, "escapes"):
            load_harness_runtime(self.env)


if __name__ == "__main__":
    unittest.main()
