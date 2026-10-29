from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from run_env_command import attempt_directory, status


@unittest.skipUnless(os.name == "posix", "POSIX process groups")
class EnvCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / "run"
        self.run.mkdir()
        self.script = Path(__file__).with_name("run_env_command.py")

    def call(self, action: str, *args: str) -> tuple[int, dict]:
        result = subprocess.run(
            [sys.executable, str(self.script), action, "--run-dir", str(self.run), *args],
            capture_output=True, text=True, timeout=8,
        )
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_short_failure_pipeline_and_old_rc(self) -> None:
        (self.root / "build.rc").write_text("0")
        for command, expected in [("pwd", 0), ("exit 7", 7), ("false | cat", 1)]:
            with self.subTest(command=command):
                code, state = self.call("start", "--cwd", str(self.root), "--command", command, "--wait", "3")
                self.assertEqual(state["returncode"], expected)
                self.assertEqual(code, 0 if expected == 0 else 1)
                self.assertTrue(state["processes_ended"])
                self.assertIsNotNone(state["started_at"])
                self.assertIsNotNone(state["ended_at"])
                if expected == 0:
                    self.assertEqual(Path(state["log"]).read_text().strip(), str(self.root))

    def test_concurrent_same_command_and_silent_wait_does_not_kill(self) -> None:
        states = []
        for cwd in (self.root, self.run):
            _, state = self.call("start", "--cwd", str(cwd), "--command", "sleep 0.6; pwd", "--wait", "0.1")
            self.assertEqual(state["status"], "running")
            self.assertFalse(state["processes_ended"])
            states.append(state)
        self.assertNotEqual(states[0]["attempt"], states[1]["attempt"])
        for state in states:
            _, finished = self.call("status", "--attempt", state["attempt"], "--wait", "3")
            self.assertEqual(finished["status"], "succeeded")
            self.assertEqual(Path(finished["log"]).read_text().strip(), state["cwd"])

    def test_transient_ps_failure_does_not_orphan_a_finished_attempt(self) -> None:
        shim = self.root / "ps-shim"
        shim.mkdir()
        (shim / "count").write_text("0")
        (shim / "ps").write_text(
            '#!/bin/sh\ndir=$(dirname "$0")\nn=$(cat "$dir/count")\nn=$((n + 1))\n'
            'echo "$n" > "$dir/count"\n[ "$n" = 2 ] && exit 1\nexec /bin/ps "$@"\n'
        )
        (shim / "ps").chmod(0o755)
        # The second ps call (first post-exit inspection) fails once; the
        # attempt must still reach a confirmed terminal state.
        environment = dict(os.environ, PATH=f"{shim}{os.pathsep}{os.environ['PATH']}")
        result = subprocess.run(
            [sys.executable, str(self.script), "start", "--run-dir", str(self.run),
             "--cwd", str(self.root), "--command", "pwd", "--wait", "5"],
            capture_output=True, text=True, timeout=8, env=environment,
        )
        self.assertTrue(result.stdout, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state["status"], "succeeded")
        self.assertTrue(state["processes_ended"])

    def test_query_trail_records_calls_with_log_progress(self) -> None:
        _, state = self.call("start", "--cwd", str(self.root),
                             "--command", "echo first; sleep 0.4; echo second", "--wait", "0.1")
        self.assertEqual(state["status"], "running")
        _, finished = self.call("status", "--attempt", state["attempt"], "--wait", "3")
        self.assertEqual(finished["status"], "succeeded")
        trail = (attempt_directory(self.run, state["attempt"]) / "queries.jsonl").read_text()
        queries = [json.loads(line) for line in trail.splitlines()]
        self.assertEqual([query["action"] for query in queries], ["start", "status"])
        self.assertEqual(queries[0]["status_seen"], "running")
        self.assertEqual(queries[1]["status_seen"], "succeeded")
        self.assertTrue(queries[1]["processes_ended"])
        self.assertGreater(queries[0]["log_bytes"], 0)
        self.assertGreaterEqual(queries[1]["log_bytes"], queries[0]["log_bytes"])

    def test_stop_waits_for_children_even_after_shell_exit(self) -> None:
        _, state = self.call("start", "--cwd", str(self.root), "--command", "sleep 5 & exit 0", "--wait", "0.2")
        self.assertEqual(state["status"], "running")
        code, stopped = self.call("stop", "--attempt", state["attempt"], "--wait", "3")
        self.assertEqual(code, 1)
        self.assertEqual(stopped["status"], "failed")
        self.assertTrue(stopped["stop_confirmed"])
        self.assertTrue(stopped["processes_ended"])

    def test_lost_owner_is_unknown_and_stop_does_not_signal_saved_pid(self) -> None:
        _, state = self.call("start", "--cwd", str(self.root), "--command", "sleep 0.5; touch finished", "--wait", "0.1")
        self.assertEqual(state["status"], "running")
        # Kill only the monitor created by this test, while its bounded child
        # finishes normally. An unrelated process must never be a stop target.
        os.kill(state["supervisor_pid"], signal.SIGKILL)
        deadline = time.monotonic() + 3
        while not (self.root / "finished").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((self.root / "finished").exists())
        state_path = attempt_directory(self.run, state["attempt"]) / "state.json"
        recorded = json.loads(state_path.read_text())
        recorded["supervisor_pid"] = os.getpid()
        state_path.write_text(json.dumps(recorded))
        code, lost = self.call("stop", "--attempt", state["attempt"], "--wait", "0")
        self.assertEqual(code, 2)
        self.assertEqual(lost["status"], "unknown")
        self.assertFalse(lost["processes_ended"])
        self.assertFalse(lost["stop_requested"])
        with self.assertRaises(ValueError):
            status(self.run, "../old")


if __name__ == "__main__":
    unittest.main()
