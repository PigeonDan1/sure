from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("sure_skill_run", Path(__file__).with_name("run.py"))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def options(command, **kwargs):
    return argparse.Namespace(
        command=command, arguments=kwargs.get("arguments", ""), args_file=None,
        output_dir=kwargs.get("output_dir"), trust=kwargs.get("trust", False),
        dry_run=kwargs.get("dry_run", False),
    )


class SureSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "node_modules/tsx").mkdir(parents=True)
        self.workflows = ["feed", "onboard", "trans", "approve", "infer", "eval", "agent_eval"]
        for name in self.workflows:
            path = self.root / "sure/skills" / f"sure_{name}"
            path.mkdir(parents=True)
            (path / "sure.skill.json").write_text(json.dumps({
                "command": f"/sure_{name}", "prompt": "SKILL.md",
            }))

    def test_installed_checkout_exposes_all_commands_and_real_manuals(self):
        installed = runner.commands(runner.ROOT)
        self.assertEqual(set(installed), {f"sure_{name}" for name in self.workflows + ["init", "resume"]})
        for item in installed.values():
            if item["kind"] == "workflow":
                self.assertTrue(Path(item["manual"]).is_file())
        discovered = runner.ROOT / ".agents/skills/sure/SKILL.md"
        self.assertEqual(discovered.resolve(), runner.ROOT / "skills/sure/SKILL.md")

    def test_all_commands_plan_without_running_a_provider(self):
        for name in self.workflows + ["init"]:
            with self.subTest(name=name), patch.object(runner.subprocess, "run") as launch:
                result, code = runner.invoke(options(name, dry_run=True), self.root)
                self.assertEqual(code, 0)
                self.assertEqual(result["status"], "planned")
                self.assertEqual(result["command"], f"/sure_{name}")
                self.assertIn("--no-approve", result["argv"])
                launch.assert_not_called()
        self.assertFalse((self.root / ".sure").exists())

    def test_native_success_and_non_success_statuses(self):
        for status in ["success", "failed", "incomplete", "cancelled", "running"]:
            output = self.root / status

            def launch(argv, **kwargs):
                self.assertEqual(kwargs["cwd"], self.root)
                self.assertIn("--approve", argv)
                tokens = shlex.split(argv[-1])
                output_arg = next(token for token in tokens if token.startswith("output_dir="))
                target = Path(output_arg.split("=", 1)[1])
                target.mkdir(parents=True)
                (target / "result.json").write_text(json.dumps({
                    "schema": "sure.run_result.v1", "command": "/sure_eval",
                    "status": status, "run_id": "fixture-run", "run_dir": "/fixture/run",
                }))
                return subprocess.CompletedProcess(argv, 0)

            with patch.object(runner.subprocess, "run", launch):
                result, code = runner.invoke(options("eval", output_dir=output, trust=True), self.root)
                self.assertEqual(result["status"], status)
                self.assertEqual(code, 0 if status == "success" else 1)
                self.assertEqual(result["run_id"], "fixture-run")

    def test_no_result_cannot_be_mistaken_for_success(self):
        with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            result, code = runner.invoke(options("feed"), self.root)
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "incomplete")

    def test_rejects_stale_outputs_and_secret_arguments(self):
        output = self.root / "old"
        output.mkdir()
        (output / "result.json").write_text('{"status":"success"}')
        for opt in [options("eval", output_dir=output), options("init", arguments="--api-key dummy")]:
            with self.assertRaises(ValueError), patch.object(runner.subprocess, "run") as launch:
                runner.invoke(opt, self.root)
            launch.assert_not_called()

    def test_init_requires_a_fresh_valid_manifest(self):
        path = self.root / ".sure/init.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"trusted": True, "uvOk": True, "defaultProvider": "fixture", "defaultModel": "fixture"}))
        with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            _, code = runner.invoke(options("init"), self.root)
            self.assertEqual(code, 1)

        def initialize(argv, **kwargs):
            path.write_text(json.dumps({"trusted": True, "uvOk": True, "defaultProvider": "new-fixture", "defaultModel": "fixture"}))
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(runner.subprocess, "run", initialize):
            result, code = runner.invoke(options("init"), self.root)
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "success")

    def test_resume_checks_the_exact_run_for_fresh_terminal_evidence(self):
        run = self.root / ".sure/runs/fixture-run/run.json"
        run.parent.mkdir(parents=True)
        run.write_text('{"status":"incomplete"}')

        def resume(argv, **kwargs):
            self.assertEqual(argv[-1], "/sure_resume fixture-run")
            run.write_text(json.dumps({"status": "success", "runId": "fixture-run", "runDir": str(run.parent)}))
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(runner.subprocess, "run", resume):
            result, code = runner.invoke(options("resume", arguments="fixture-run"), self.root)
            self.assertEqual(code, 0)
            self.assertEqual(result["run_id"], "fixture-run")
        with self.assertRaises(ValueError):
            runner.invoke(options("resume", arguments="../other"), self.root)

    def test_real_cli_reports_untrusted_feed_and_incomplete_init(self):
        env = {key: value for key, value in os.environ.items() if key in {
            "PATH", "HOME", "LANG", "LD_LIBRARY_PATH", "TMPDIR", "TZ", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
        }}
        env["PI_CODING_AGENT_DIR"] = str(self.root / "isolated-agent")
        for command, extra, diagnostic in [
            ("feed", [], "trusted"), ("init", ["--trust"], "No agent selected"),
        ]:
            completed = subprocess.run(
                [sys.executable, str(Path(runner.__file__)), command, *extra],
                cwd=self.root, env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["status"], "incomplete")
            self.assertEqual(receipt["exit_code"], 0)
            logs = Path(receipt["receipt_dir"])
            native = (logs / "harness.jsonl").read_text() + (logs / "harness.stderr.log").read_text()
            self.assertIn(diagnostic, native)


if __name__ == "__main__":
    unittest.main()
