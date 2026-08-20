#!/usr/bin/env python3
from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from evaluation_runtime import (
    _make_group_writable,
    _wrapper,
    evaluation_child_environment,
    evaluation_runtime_from_eval_input,
)


class EvaluationRuntimeTests(unittest.TestCase):
    def test_materialized_runtime_is_group_writable_without_inventing_execute_bits(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            data = root / "data.json"
            executable = root / "bin" / "python"
            executable.parent.mkdir()
            data.write_text("{}\n", encoding="utf-8")
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            root.chmod(0o700)
            executable.parent.chmod(0o700)
            data.chmod(0o600)
            executable.chmod(0o700)

            _make_group_writable(root)

            self.assertEqual(stat.S_IMODE(root.stat().st_mode) & 0o070, 0o070)
            self.assertEqual(stat.S_IMODE(executable.parent.stat().st_mode) & 0o070, 0o070)
            self.assertEqual(stat.S_IMODE(data.stat().st_mode) & 0o070, 0o060)
            self.assertEqual(stat.S_IMODE(executable.stat().st_mode) & 0o070, 0o070)

            inherited = root / "created-after-finalize.txt"
            inherited.write_text("ok\n", encoding="utf-8")
            self.assertEqual(stat.S_IMODE(inherited.stat().st_mode) & 0o060, 0o060)

    def test_wrapper_does_not_leak_parent_pythonhome(self) -> None:
        text = _wrapper(
            {
                "runtime_root": "/repo/sure/.runtime/evaluation/demo",
                "harness_runtime_root": "/repo/sure/.runtime/harness/demo",
                "engine_root": "/repo/sure/external/sure-evaluation",
                "dynamic_loader": "/lib64/ld-linux-x86-64.so.2",
            }
        )
        self.assertIn("unset PYTHONHOME PYTHONEXECUTABLE", text)
        self.assertIn("/site-packages", text)
        self.assertIn("/sure-evaluation'/src", text)
        self.assertIn("--library-path", text)
        self.assertNotIn("export LD_LIBRARY_PATH='/repo", text)
        self.assertIn("_sure_eval_parent_ld", text)

    def test_child_environment_removes_only_harness_library_path(self) -> None:
        harness_root = "/repo/sure/.runtime/harness/demo"
        env = evaluation_child_environment(
            {
                "SURE_HARNESS_RUNTIME_ROOT": harness_root,
                "LD_LIBRARY_PATH": f"{harness_root}/base/lib:/usr/local/cuda/lib64:/opt/model/lib",
            }
        )
        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/local/cuda/lib64:/opt/model/lib")

    def test_non_external_input_has_no_evaluation_runtime(self) -> None:
        self.assertIsNone(
            evaluation_runtime_from_eval_input(
                {"evaluation": {"backend": "legacy"}},
                prepare=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
