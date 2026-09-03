#!/usr/bin/env python3
"""Tests for the read-only smoke gate over execution_result.json."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_smoke


def _result(**overrides: object) -> dict:
    payload = {
        "job_status": "succeeded",
        "exit_code": 0,
        "execution_path": "local_docker",
        "failed_stage": "",
        "datasets": [{"dataset": "demo__v1", "expected": 4, "generated": 4, "valid": 4}],
    }
    payload.update(overrides)
    return payload


class SmokeResultTests(unittest.TestCase):
    def test_stages_through_smoke_stop_before_generate(self) -> None:
        self.assertEqual(run_smoke.STAGES_THROUGH_SMOKE, ("guards", "tool_name", "config", "prepare", "materialize", "smoke"))

    def test_a_succeeded_run_passes_with_its_generated_count(self) -> None:
        payload = run_smoke.smoke_result(_result())
        self.assertTrue(payload["smoke_passed"])
        self.assertEqual(payload["sample_count"], 4)
        self.assertEqual(payload["failures"], [])

    def test_a_failure_in_the_smoke_stage_fails_the_gate(self) -> None:
        payload = run_smoke.smoke_result(_result(job_status="failed", exit_code=1, failed_stage="smoke", datasets=[]))
        self.assertFalse(payload["smoke_passed"])
        self.assertTrue(any("'smoke'" in failure for failure in payload["failures"]), payload["failures"])

    def test_a_failure_after_the_smoke_stage_still_passes_the_smoke_gate(self) -> None:
        payload = run_smoke.smoke_result(_result(job_status="failed", exit_code=1, failed_stage="generate"))
        self.assertTrue(payload["smoke_passed"], payload["failures"])
        self.assertEqual(payload["exit_code"], 1)

    def test_a_running_job_is_not_a_smoke_result(self) -> None:
        payload = run_smoke.smoke_result(_result(job_status="running", exit_code=None))
        self.assertFalse(payload["smoke_passed"])
        self.assertIn("still running", payload["failures"][0])

    def test_a_success_without_predictions_is_refused(self) -> None:
        payload = run_smoke.smoke_result(_result(datasets=[]))
        self.assertFalse(payload["smoke_passed"])


class SmokeGateMainTests(unittest.TestCase):
    def run_main(self, run_dir: Path) -> tuple[int, dict]:
        produces = run_dir / "artifacts" / "smoke_test_result.json"
        old_argv = sys.argv
        try:
            sys.argv = ["run_smoke.py", "--run-dir", str(run_dir), "--produces", str(produces)]
            rc = run_smoke.main()
        finally:
            sys.argv = old_argv
        return rc, json.loads(produces.read_text(encoding="utf-8"))

    def test_a_missing_execution_result_fails_and_names_the_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "artifacts").mkdir(parents=True)
            rc, payload = self.run_main(run_dir)
        self.assertEqual(rc, 1)
        self.assertFalse(payload["smoke_passed"])
        self.assertIn("run_infer.py", payload["failures"][0])

    def test_a_succeeded_result_writes_a_passing_smoke_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            stdout_log = artifacts / "local_execution.stdout.log"
            stdout_log.write_text("Smoke test passed (4 valid rows)\n", encoding="utf-8")
            (artifacts / "execution_result.json").write_text(json.dumps(_result(stdout_log=str(stdout_log))), encoding="utf-8")
            rc, payload = self.run_main(run_dir)
        self.assertEqual(rc, 0)
        self.assertTrue(payload["smoke_passed"])
        self.assertEqual(payload["sample_count"], 4)
        self.assertIn("Smoke test passed", payload["stdout_excerpt"])
        self.assertEqual(set(payload), {"smoke_passed", "sample_count", "exit_code", "stdout_excerpt", "stderr_excerpt", "failures"})

    def test_the_gate_never_launches_anything(self) -> None:
        # The bash-era gate ran the entrypoint a second time. This one only reads.
        self.assertFalse(hasattr(run_smoke, "subprocess"))
        self.assertFalse(hasattr(run_smoke, "build_local_container_command"))
        self.assertFalse(hasattr(run_smoke, "build_local_python_command"))


if __name__ == "__main__":
    unittest.main()
