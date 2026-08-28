#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_local_execution


class LocalExecutionExitTests(unittest.TestCase):
    def run_with_command(self, command: list[str]) -> tuple[int, dict, dict]:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            entrypoint = artifacts / "run.sh"
            entrypoint.write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "execution_surface.json").write_text(
                json.dumps(
                    {
                        "entrypoint_path": str(entrypoint),
                        "execution": {
                            "requested": "local",
                            "planned": "local",
                            "path_planned": "local_docker",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (artifacts / "eval_input_resolved.json").write_text("{}", encoding="utf-8")
            argv = ["run_local_execution.py", "--run-dir", str(run_dir)]
            with (
                patch.object(sys, "argv", argv),
                patch.object(run_local_execution, "_vc_available", return_value=False),
                patch.object(
                    run_local_execution,
                    "build_local_container_command",
                    return_value=(command, {"image_ref": "test"}),
                ),
            ):
                return_code = run_local_execution.main()
            result = json.loads((artifacts / "execution_result.json").read_text(encoding="utf-8"))
            submit = json.loads((artifacts / "submit_result.json").read_text(encoding="utf-8"))
            return return_code, result, submit

    def test_command_failure_propagates_to_execution_result(self) -> None:
        return_code, result, submit = self.run_with_command(["bash", "-c", "exit 23"])
        self.assertEqual(return_code, 23)
        self.assertEqual(result["exit_code"], 23)
        self.assertEqual(result["job_status"], "failed")
        self.assertEqual(submit["vc_image"], "test")

    def test_site_docker_wrapper_text_propagates_inner_exit(self) -> None:
        return_code, result, _ = self.run_with_command(
            ["bash", "-c", "printf 'Error: exit status 37\\n'; exit 0"]
        )
        self.assertEqual(return_code, 37)
        self.assertEqual(result["exit_code"], 37)
        self.assertEqual(result["job_status"], "failed")

    def test_local_python_dispatch_never_builds_a_docker_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            artifacts = run_dir / "artifacts"
            artifacts.mkdir(parents=True)
            entrypoint = artifacts / "run.sh"
            entrypoint.write_text("#!/bin/sh\n", encoding="utf-8")
            (artifacts / "execution_surface.json").write_text(
                json.dumps(
                    {
                        "entrypoint_path": str(entrypoint),
                        "execution": {
                            "requested": "local",
                            "planned": "local",
                            "path_planned": "local_python",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (artifacts / "eval_input_resolved.json").write_text(
                json.dumps({"runtime": {"model_runtime": "python"}}),
                encoding="utf-8",
            )
            argv = ["run_local_execution.py", "--run-dir", str(run_dir)]
            with (
                patch.object(sys, "argv", argv),
                patch.object(run_local_execution, "_vc_available", return_value=False),
                patch.object(
                    run_local_execution,
                    "build_local_python_command",
                    return_value=(
                        ["bash", "-c", "exit 0"],
                        {},
                        {"runtime_kind": "python"},
                    ),
                ) as python_builder,
                patch.object(run_local_execution, "build_local_container_command") as container_builder,
            ):
                return_code = run_local_execution.main()

            self.assertEqual(return_code, 0)
            python_builder.assert_called_once()
            container_builder.assert_not_called()
            result = json.loads((artifacts / "execution_result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["execution_path"], "local_python")
            self.assertEqual(result["runtime_kind"], "python")


if __name__ == "__main__":
    unittest.main()
