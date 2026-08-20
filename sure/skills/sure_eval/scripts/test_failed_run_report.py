#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_run_report


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class FailedRunReportTests(unittest.TestCase):
    def test_pre_submit_smoke_failure_can_be_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            artifacts = run_dir / "artifacts"
            report = artifacts / "main_agent_run_report.json"
            write_json(
                artifacts / "smoke_test_result.json",
                {"smoke_passed": False, "exit_code": 41, "failures": ["Harness import failed"]},
            )
            write_json(
                artifacts / "assessment_report.json",
                {"status": "failed", "anomaly_detected": True, "user_confirmed": True},
            )
            write_json(
                report,
                {
                    "run_id": "failed-run",
                    "timestamp": "now",
                    "task_type": "evaluate_existing_model",
                    "goal": "bounded evaluation",
                    "selected_datasets": ["dataset__v1"],
                    "executed_steps": ["execution_readiness", "smoke_test"],
                    "status": "failed",
                    "report_persisted": True,
                    "execution_path_actual": "blocked_before_submit",
                    "execution": {
                        "requested": "local",
                        "actual": "not_submitted",
                        "path_actual": "blocked_before_submit",
                        "failure_class": "smoke_test_failed",
                    },
                    "next_action": "Repair the classified runtime and rerun from smoke_test.",
                },
            )
            with patch.object(
                sys,
                "argv",
                [
                    "check_run_report.py",
                    "--run-dir",
                    str(run_dir),
                    "--produces",
                    str(report),
                ],
            ):
                self.assertEqual(check_run_report.main(), 0)


if __name__ == "__main__":
    unittest.main()
