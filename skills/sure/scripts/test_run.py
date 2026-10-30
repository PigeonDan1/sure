from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("sure_skill_run", Path(__file__).with_name("run.py"))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
WORKFLOWS = ["feed", "onboard", "trans", "approve", "infer", "eval", "agent_eval"]


def options(command, **kwargs):
    values = dict(command=command, arguments="", args_file=None, output_dir=None, trust=False,
                  dry_run=False, timeout_seconds=30.0, kill_grace_seconds=0.3)
    values.update(kwargs)
    return argparse.Namespace(**values)


def fixture_package(root, name, project=False):
    directory = root / (".sure/skills" if project else "sure/skills") / ("sure_" + name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text("Offline transport fixture.\n")
    manifest = {"name": "fixture-" + name, "command": "/sure_" + name, "prompt": "SKILL.md"}
    (directory / "sure.skill.json").write_text(json.dumps(manifest))
    return directory


class SureSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sure-transport-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for command in WORKFLOWS:
            fixture_package(self.root, command)

    def clean_env(self):
        env = {key: value for key, value in os.environ.items() if key in {
            "PATH", "HOME", "LANG", "LD_LIBRARY_PATH", "TMPDIR", "TZ", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
        }}
        env.update(PI_CODING_AGENT_DIR=str(self.root / "agent-config"), PI_OFFLINE="1")
        if (self.root / "config/site.local.yaml").exists():
            env["SURE_SITE_POLICY"] = str(self.root / "config/site.local.yaml")
        return patch.dict(os.environ, env, clear=True)

    def test_native_discovery_override_invalid_packages_and_all_commands(self):
        override = fixture_package(self.root, "feed", project=True)
        bad = fixture_package(self.root, "unregistered")
        result, code = runner.invoke(options("list"), self.root)
        self.assertEqual(code, 0, result)
        self.assertEqual({item["command"] for item in result["commands"]},
                         {"/sure_" + name for name in WORKFLOWS + ["init", "resume"]})
        feed = next(item for item in result["commands"] if item["command"] == "/sure_feed")
        self.assertEqual(feed["source"], "project")
        self.assertEqual(Path(feed["manual"]), override / "SKILL.md")
        self.assertTrue(result["diagnostics"])
        self.assertTrue(bad.exists())

    def test_plans_preserve_unicode_quotes_backslashes_and_apostrophes(self):
        for name in WORKFLOWS + ["init", "resume"]:
            arguments = "fixture-run" if name == "resume" else "model_input_path=/tmp/O'Brien/input.yaml"
            output = None if name in ("init", "resume") else self.root / '中文 "结果" \\ path'
            result, code = runner.invoke(options(name, arguments=arguments, output_dir=output, dry_run=True), self.root)
            self.assertEqual(code, 0, result)
            request = json.loads(result["argv"][-1].split(" ", 1)[1])
            self.assertEqual(request["arguments"], arguments)
            if output:
                self.assertEqual(request["output_dir"], str(output))
        self.assertFalse((self.root / ".sure").exists())

    def test_secret_and_embedded_output_parameters_are_rejected_without_persisting_secrets(self):
        for arguments in ["--api-key secret-fixture-value", "output_dir=/tmp/untrusted", "-output_dir /tmp/untrusted"]:
            result, code = runner.invoke(options("feed", arguments=arguments), self.root)
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(Path(result["receipt_path"]).exists())
        for path in (self.root / ".sure/agent-invocations").rglob("*"):
            if path.is_file():
                self.assertNotIn("secret-fixture-value", path.read_text(errors="replace"))

    def test_native_init_reply_is_correlated_not_read_from_global_file(self):
        request = {"command": "sure_init", "invocation_id": "a" * 32}
        base = {"schema": "sure.agent.dispatch.v1", **request, "status": "success", "expected_model": "wanted",
                "init_manifest": {"trusted": True, "uvOk": True, "defaultProvider": "fixture", "defaultModel": "wanted"}}
        self.assertEqual(runner.validate_result(self.root, request, base)["status"], "success")
        for reply in [[], None, {**base, "invocation_id": "b" * 32},
                      {**base, "init_manifest": {**base["init_manifest"], "defaultModel": "unrelated"}}]:
            with self.assertRaises(ValueError):
                runner.validate_result(self.root, request, reply)

    def test_malformed_results_and_missing_manifest_do_not_pass(self):
        request = {"command": "sure_eval", "invocation_id": "a" * 32, "output_dir": str(self.root / "output")}
        run_dir = self.root / ".sure/runs/fixture"
        run_dir.mkdir(parents=True)
        record = {"runId": "fixture", "runDir": str(run_dir), "invocationId": "a" * 32, "command": "sure_eval",
                  "skillName": "fixture-eval", "status": "success", "finishedAt": runner.now(), "outputDir": request["output_dir"]}
        (run_dir / "run.json").write_text(json.dumps(record))
        output = Path(request["output_dir"])
        output.mkdir()
        reply = {"schema": "sure.agent.dispatch.v1", "invocation_id": "a" * 32, "command": "sure_eval",
                 "status": "accepted", "run_id": "fixture", "run_dir": str(run_dir), "run_command": "sure_eval"}
        valid = {"schema": "sure.run_result.v1", "invocation_id": "a" * 32, "command": "/sure_eval",
                 "run_id": "fixture", "run_dir": str(run_dir), "status": "success", "products": []}
        for payload in [[], None, {}, {"schema": "sure.run_result.v1", "command": "/sure_eval", "status": "success"}, valid]:
            (output / "result.json").write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                runner.validate_result(self.root, request, reply)

    def install_native_fixture(self):
        fixture = runner.ROOT / "packages/coding-agent/test/fixtures/sure-agent"
        extensions = self.root / ".pi/extensions"
        extensions.mkdir(parents=True)
        shutil.copyfile(fixture / "provider.ts", extensions / "provider.ts")
        (extensions.parent / "settings.json").write_text(json.dumps({
            "defaultProvider": "sure-local-fixture", "defaultModel": "fixture-model",
        }))
        config = self.root / "config"
        config.mkdir()
        (config / "site.local.yaml").write_text(json.dumps({
            "schema": "sure.site.policy.v1", "site_id": "fixture", "policy_version": 1,
            "storage": {"approved_models_roots": [str(self.root / "approved/models")],
                        "approved_results_roots": [str(self.root / "approved/results")],
                        "forbidden_output_roots": [str(self.root / "approved")], "runtime_root": str(self.root / "runtime")},
            "datasets": {"allowed_source_roots": [str(self.root / "datasets")]},
            "execution": {"surfaces": ["local"], "local_runtimes": ["python"]},
        }))
        for name in WORKFLOWS:
            package = self.root / "sure/skills" / ("sure_" + name)
            shutil.copyfile(fixture / "gates.ts", package / "gates.ts")
            manifest = json.loads((package / "sure.skill.json").read_text())
            manifest["hooks"] = {"pre_finish": [{"module": "gates.ts", "handler": "preFinish"}]}
            manifest["artifacts"] = [{"type": "proof", "path": "artifacts/proof.json", "required": True}]
            (package / "sure.skill.json").write_text(json.dumps(manifest))

    def test_real_wrapper_native_cli_gate_repair_and_success_for_all_workflows(self):
        self.install_native_fixture()
        with self.clean_env():
            for name in WORKFLOWS:
                with self.subTest(command=name):
                    output = self.root / ('中文 "结果" \\ ' + name)
                    result, code = runner.invoke(options(name, trust=True, output_dir=output), self.root)
                    self.assertEqual(code, 0, result)
                    self.assertEqual(result["status"], "success")
                    self.assertTrue((output / "result.json").is_file())
                    self.assertTrue((output / "artifacts/gate-observed.json").is_file())
                    self.assertEqual(result["trace_summary"]["finish_repairs"], 1)
                    self.assertEqual(result["trace_summary"]["tool_calls"], 1)
                    self.assertTrue(Path(result["manifest_path_snapshot"]).is_file())
                    self.assertEqual(runner.digest(result["manifest_path_snapshot"]), result["manifest_path_snapshot_sha256"])

    def test_real_native_trust_init_and_forbidden_output_refusals(self):
        self.install_native_fixture()
        with self.clean_env():
            for opt in [options("feed"), options("init", trust=True),
                        options("eval", trust=True, output_dir=self.root / "approved/forbidden")]:
                result, code = runner.invoke(opt, self.root)
                self.assertEqual(code, 1, result)
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(Path(result["receipt_path"]).is_file())
        self.assertFalse((self.root / "approved/forbidden").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX lock semantics")
    def test_configuration_and_resume_output_locks_reject_competing_calls(self):
        path = self.root / "lock"
        with runner.configuration_lock(path):
            with self.assertRaises(ValueError):
                with runner.configuration_lock(path, exclusive=False):
                    self.fail("Conflicting lock acquired")

    def test_timeout_stops_a_child_process(self):
        stdout, stderr = self.root / "stdout", self.root / "stderr"
        code, reason = runner.managed_run([sys.executable, "-c", "import time; time.sleep(30)"],
                                         self.root, stdout, stderr, time.monotonic() + 0.2, 0.1, [None])
        self.assertNotEqual(code, 0)
        self.assertEqual(reason, "timed_out")

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_cancel_stops_descendant_process_group(self):
        pid_path = self.root / "descendant.pid"
        script = "import os, pathlib, subprocess, sys, time; child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)"
        stopped = [None]
        pid = subprocess.Popen([sys.executable, "-c", script, str(pid_path)], start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not pid_path.exists():
                time.sleep(0.02)
            runner.stop_process_tree(pid)
            pid.wait(timeout=5)
            descendant = int(pid_path.read_text())
            for _ in range(30):
                try:
                    os.kill(descendant, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                self.fail("Descendant process survived cancellation")
        finally:
            if pid.poll() is None:
                runner.stop_process_tree(pid, force=True)

    def test_invalid_limits_are_json_errors(self):
        for timeout in [0, -1, float("nan"), float("inf")]:
            result, code = runner.invoke(options("feed", timeout_seconds=timeout), self.root)
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
