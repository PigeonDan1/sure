#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sure_eval.agent import vc_submitter  # noqa: E402
import resolve_eval_input  # noqa: E402


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


VC_EXECUTION = {"requested": "vc", "planned": "vc", "path_planned": "vc_submit"}
LOCAL_EXECUTION = {"requested": "local", "planned": "local", "path_planned": "local_bash"}


class ValidateVcPartitionTests(unittest.TestCase):
    def setUp(self):
        self.original = vc_submitter._run_cmd
        self.addCleanup(setattr, vc_submitter, "_run_cmd", self.original)
        self.calls = []

    def stub_vc_info(self, result=None, error=None):
        def _run_cmd(args, check=True, timeout=None):
            self.calls.append((args, timeout))
            if error is not None:
                raise error
            return result

        vc_submitter._run_cmd = _run_cmd

    def test_allowed_partition_passes(self):
        self.stub_vc_info(result=FakeCompleted(0, VC_INFO_U_OUTPUT))
        resolve_eval_input._validate_vc_partition({"partition": "pdgpu-3090"}, VC_EXECUTION)

    def test_unknown_partition_raises_with_allowed_list(self):
        self.stub_vc_info(result=FakeCompleted(0, VC_INFO_U_OUTPUT))
        with self.assertRaises(resolve_eval_input.EvalInputError) as ctx:
            resolve_eval_input._validate_vc_partition({"partition": "pdgpu-9999"}, VC_EXECUTION)
        message = str(ctx.exception)
        self.assertIn('vc_partition "pdgpu-9999" is not in your allowed partitions.', message)
        self.assertIn("Allowed: pdgpu-2080ti, pdgpu-3090, pdgpu-4090", message)

    def test_query_timeout_skips_validation(self):
        self.stub_vc_info(error=subprocess.TimeoutExpired(cmd="vc info -u", timeout=30))
        resolve_eval_input._validate_vc_partition({"partition": "pdgpu-9999"}, VC_EXECUTION)

    def test_missing_vc_binary_skips_validation(self):
        self.stub_vc_info(error=FileNotFoundError("vc"))
        resolve_eval_input._validate_vc_partition({"partition": "pdgpu-9999"}, VC_EXECUTION)

    def test_empty_partition_list_skips_validation(self):
        self.stub_vc_info(result=FakeCompleted(0, "[Partition]\n------\n[Next]\n"))
        resolve_eval_input._validate_vc_partition({"partition": "pdgpu-9999"}, VC_EXECUTION)

    def test_local_plan_never_queries_vc(self):
        self.stub_vc_info(result=FakeCompleted(0, VC_INFO_U_OUTPUT))
        resolve_eval_input._validate_vc_partition({"partition": "pdgpu-9999"}, LOCAL_EXECUTION)
        self.assertEqual(self.calls, [])

    def test_no_partition_is_noop(self):
        self.stub_vc_info(result=FakeCompleted(0, VC_INFO_U_OUTPUT))
        resolve_eval_input._validate_vc_partition({"partition": ""}, VC_EXECUTION)
        self.assertEqual(self.calls, [])

    def test_timeout_value_is_30(self):
        self.stub_vc_info(result=FakeCompleted(0, VC_INFO_U_OUTPUT))
        resolve_eval_input._validate_vc_partition({"partition": "pdgpu-3090"}, VC_EXECUTION)
        self.assertEqual(self.calls[0][1], 30)


if __name__ == "__main__":
    unittest.main()
