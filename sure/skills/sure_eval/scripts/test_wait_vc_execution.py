#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import wait_vc_execution as waiter


class WaitVcExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run_dir = Path(self.temporary.name) / "run"
        self.artifacts = self.run_dir / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.token = "a" * 32
        self.terminal_relative = f"artifacts/vc_terminal_status.{self.token}.json"
        self.terminal_path = self.run_dir / self.terminal_relative
        self.submit = {
            "execution_path": "vc_submit",
            "execution_requested": "vc",
            "vc_job_id": "job-test",
            "submission_token": self.token,
            "terminal_status_path": self.terminal_relative,
            "submitted_at": "2026-08-30T00:00:00+00:00",
            "host": "job-test",
            "command": "bash run_evaluation.sh",
            "cwd": "/workspace",
            "stdout_log": "vc_logs/job.log",
            "stderr_log": "vc_logs/job.log",
            "device_request": "auto",
            "device_actual": "cuda:0",
            "cuda_visible_devices": "",
        }

    def write_sentinel(self, *, exit_code: int, token: str | None = None) -> None:
        self.terminal_path.write_text(
            json.dumps(
                {
                    "schema": "sure.eval.vc_terminal_status.v1",
                    "submission_token": token or self.token,
                    "job_status": "succeeded" if exit_code == 0 else "failed",
                    "exit_code": exit_code,
                    "started_at": "2026-08-30T00:00:01Z",
                    "ended_at": "2026-08-30T00:00:03Z",
                    "duration_seconds": 2,
                }
            ),
            encoding="utf-8",
        )

    def wait(self, **overrides: object) -> dict:
        options = {
            "run_dir": self.run_dir,
            "submit": self.submit,
            "timeout_seconds": 0.0,
            "poll_interval_seconds": 0.1,
            "terminal_grace_seconds": 0.0,
            "run_vc": lambda command: subprocess.CompletedProcess(command, 0, "", ""),
        }
        options.update(overrides)
        return waiter.wait_for_vc_execution(**options)

    def test_success_sentinel_is_authoritative(self) -> None:
        self.write_sentinel(exit_code=0)

        result = self.wait()

        self.assertEqual(result["job_status"], "succeeded")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["completion_source"], "terminal_sentinel")
        self.assertFalse(result["timed_out"])

    def test_nonzero_sentinel_records_terminal_failure(self) -> None:
        self.write_sentinel(exit_code=23)

        result = self.wait()

        self.assertEqual(result["job_status"], "failed")
        self.assertEqual(result["exit_code"], 23)
        self.assertEqual(waiter._validation_errors(result, self.submit), [])

    def test_cleaned_pod_without_sentinel_is_failure(self) -> None:
        def run_vc(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[1] == "info":
                output = "StartTime: 2026-08-30 00:00:01\nEndTime: 2026-08-30 00:00:03\n"
            else:
                output = "\u672a\u67e5\u5230\u8be5\u4efb\u52a1\u4fe1\u606f\nNone\n"
            return subprocess.CompletedProcess(command, 0, output, "")

        result = self.wait(run_vc=run_vc)

        self.assertEqual(result["job_status"], "failed")
        self.assertEqual(result["completion_source"], "vc_terminal_without_sentinel")
        self.assertEqual(result["exit_code"], 1)

    def test_missing_before_observation_waits_for_sentinel(self) -> None:
        def run_vc(command: list[str]) -> subprocess.CompletedProcess[str]:
            output = (
                "job\u4e0d\u5b58\u5728: job-test"
                if command[1] == "info"
                else "\u672a\u67e5\u5230\u8be5\u4efb\u52a1\u4fe1\u606f\nNone"
            )
            return subprocess.CompletedProcess(command, 0, output, "")

        ticks = iter((0.0, 0.0))
        result = self.wait(
            timeout_seconds=10.0,
            run_vc=run_vc,
            monotonic=lambda: next(ticks),
            sleep=lambda _: self.write_sentinel(exit_code=0),
        )

        self.assertEqual(result["job_status"], "succeeded")
        self.assertEqual(result["completion_source"], "terminal_sentinel")
        self.assertEqual(result["poll_count"], 1)

    def test_missing_after_observation_is_terminal_failure(self) -> None:
        poll = 0

        def run_vc(command: list[str]) -> subprocess.CompletedProcess[str]:
            nonlocal poll
            if command[1] == "info":
                poll += 1
            if poll == 1:
                output = (
                    "StartTime: 2026-08-30 00:00:01\nEndTime:\n"
                    if command[1] == "info"
                    else "Running"
                )
            else:
                output = (
                    "job\u4e0d\u5b58\u5728: job-test"
                    if command[1] == "info"
                    else "\u672a\u67e5\u5230\u8be5\u4efb\u52a1\u4fe1\u606f\nNone"
                )
            return subprocess.CompletedProcess(command, 0, output, "")

        ticks = iter((0.0, 0.0, 1.0))
        result = self.wait(
            timeout_seconds=10.0,
            run_vc=run_vc,
            monotonic=lambda: next(ticks),
            sleep=lambda _: None,
        )

        self.assertEqual(result["job_status"], "failed")
        self.assertEqual(result["completion_source"], "vc_terminal_without_sentinel")
        self.assertEqual(result["poll_count"], 2)

    def test_vc_query_errors_are_not_terminal_evidence(self) -> None:
        result = self.wait(
            run_vc=lambda command: subprocess.CompletedProcess(command, 1, "", "connection failed")
        )

        self.assertEqual(result["job_status"], "running")
        self.assertEqual(result["completion_source"], "wait_timeout")
        self.assertTrue(result["timed_out"])

    def test_timeout_stays_running_and_fails_gate_validation(self) -> None:
        def run_vc(command: list[str]) -> subprocess.CompletedProcess[str]:
            output = "StartTime: 2026-08-30 00:00:01\nEndTime:\n" if command[1] == "info" else "Running"
            return subprocess.CompletedProcess(command, 0, output, "")

        result = self.wait(run_vc=run_vc)

        self.assertEqual(result["job_status"], "running")
        self.assertEqual(result["completion_source"], "wait_timeout")
        self.assertTrue(result["timed_out"])
        self.assertIn("still running", "; ".join(waiter._validation_errors(result, self.submit)))

    def test_timeout_says_the_job_is_alive_and_the_wait_should_be_repeated(self) -> None:
        def run_vc(command: list[str]) -> subprocess.CompletedProcess[str]:
            output = "StartTime: 2026-08-30 00:00:01\nEndTime:\n" if command[1] == "info" else "Running"
            return subprocess.CompletedProcess(command, 0, output, "")

        result = self.wait(run_vc=run_vc)

        # A timeout is the waiter giving up, not the job failing. Anything that
        # reads this as a failure goes off diagnosing a healthy run.
        self.assertIn("wait_timeout", result["next_action"])
        errors = "; ".join(waiter._validation_errors(result, self.submit))
        self.assertIn("not a failure", errors)

    def test_stale_submission_token_is_rejected(self) -> None:
        self.write_sentinel(exit_code=0, token="b" * 32)

        with self.assertRaisesRegex(ValueError, "current submission token"):
            self.wait()


if __name__ == "__main__":
    unittest.main()
