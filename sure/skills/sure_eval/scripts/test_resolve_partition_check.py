#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.agent import vc_submitter  # noqa: E402


VC_INFO_U_OUTPUT = """[Quota]
GPU:  32
------------------------------
[Partition]
pdgpu-3090
pdgpu-4090
pdgpu-2080ti
------------------------------
[Storage quota]
hpc_stor03  limit: 3.00 TB used:  1.15 TB
"""


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class GetUserPartitionsTests(unittest.TestCase):
    def setUp(self):
        self.original = vc_submitter._run_cmd
        self.addCleanup(setattr, vc_submitter, "_run_cmd", self.original)

    def test_parses_partition_block_without_separator_lines(self):
        vc_submitter._run_cmd = lambda args, check=True, timeout=None: FakeCompleted(0, VC_INFO_U_OUTPUT)
        partitions = vc_submitter.get_user_partitions()
        self.assertEqual(partitions, {"pdgpu-3090", "pdgpu-4090", "pdgpu-2080ti"})

    def test_timeout_reaches_subprocess(self):
        seen = {}
        original_run = vc_submitter.subprocess.run

        def fake_run(args, **kwargs):
            seen.update(kwargs)
            return FakeCompleted(0, VC_INFO_U_OUTPUT)

        vc_submitter.subprocess.run = fake_run
        self.addCleanup(setattr, vc_submitter.subprocess, "run", original_run)
        vc_submitter.get_user_partitions(timeout=7)
        self.assertEqual(seen.get("timeout"), 7)


if __name__ == "__main__":
    unittest.main()
