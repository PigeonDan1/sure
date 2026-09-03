"""Tests for execution_result_checks.validation_errors."""
from __future__ import annotations

import unittest

from execution_result_checks import TERMINAL_JOB_STATUSES, validation_errors


class ValidationErrorsTests(unittest.TestCase):
    def test_terminal_statuses_are_the_three_local_outcomes(self) -> None:
        self.assertEqual(TERMINAL_JOB_STATUSES, {"succeeded", "failed", "partial"})

    def test_clean_success_has_no_errors(self) -> None:
        result = {"job_status": "succeeded", "exit_code": 0, "execution_path": "local_docker"}
        self.assertEqual(validation_errors(result, "local_docker"), [])

    def test_clean_failure_has_no_errors(self) -> None:
        result = {"job_status": "failed", "exit_code": 3, "execution_path": "local_python"}
        self.assertEqual(validation_errors(result, "local_python"), [])

    def test_running_is_reported_as_not_finished_rather_than_failed(self) -> None:
        result = {"job_status": "running", "exit_code": None, "execution_path": "local_docker"}
        errors = validation_errors(result, "local_docker")
        self.assertEqual(len(errors), 1)
        self.assertIn("still running", errors[0])
        self.assertIn("not a failure", errors[0])

    def test_unknown_status_is_invalid(self) -> None:
        result = {"job_status": "done", "exit_code": 0, "execution_path": "local_docker"}
        errors = validation_errors(result, "local_docker")
        self.assertEqual(errors, ["execution_result.json has invalid job_status: 'done'"])

    def test_path_mismatch_is_reported(self) -> None:
        result = {"job_status": "succeeded", "exit_code": 0, "execution_path": "local_python"}
        errors = validation_errors(result, "local_docker")
        self.assertEqual(len(errors), 1)
        self.assertIn("execution_path", errors[0])
        self.assertIn("local_docker", errors[0])

    def test_empty_expected_path_skips_the_path_comparison(self) -> None:
        result = {"job_status": "succeeded", "exit_code": 0, "execution_path": "local_python"}
        self.assertEqual(validation_errors(result, ""), [])

    def test_succeeded_with_nonzero_exit_code_is_rejected(self) -> None:
        result = {"job_status": "succeeded", "exit_code": 1, "execution_path": "local_docker"}
        self.assertEqual(
            validation_errors(result, "local_docker"),
            ["succeeded execution_result.json must declare exit_code=0"],
        )

    def test_failed_with_zero_or_missing_exit_code_is_rejected(self) -> None:
        for exit_code in (0, None):
            result = {"job_status": "failed", "exit_code": exit_code, "execution_path": "local_docker"}
            self.assertEqual(
                validation_errors(result, "local_docker"),
                ["failed execution_result.json must declare a non-zero exit_code"],
                msg=f"exit_code={exit_code!r}",
            )


if __name__ == "__main__":
    unittest.main()
