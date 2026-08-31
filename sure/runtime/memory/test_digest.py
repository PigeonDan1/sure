# sure/runtime/memory/test_digest.py
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # sure/runtime

from memory import digest, paths  # noqa: E402
from memory import index as index_lib  # noqa: E402  (ordered_entries: index.json row order)

CONFIG = paths.load_config()
UNITS = paths.load_units()
LOG_PATHS = paths.load_log_paths()
HEADER = CONFIG["inject_header"]
ONBOARD = UNITS["skills"]["sure_onboard"]
TS = "2026-08-18T12:00:00.000Z"
REPO_ROOT = Path(__file__).resolve().parents[3]

MEMORY_BLOCK = (
    f"{HEADER}\n"
    "- [confirmed] sure_onboard/no-kernel-image: CUDA arch mismatch: no kernel image is available "
    "(sure/skills/sure_onboard/references/memory/bad_cases/no-kernel-image.md)\n"
    "- [provisional] sure_onboard/torch-cu118-index: pip cannot see the cu118 wheel index "
    "(sure/memory/provisional/sure_onboard/torch-cu118-index/entry.md)"
)
RAW_BUILD_ENV_REPAIR = (
    "Gate script scripts/check_build_env.py rejected build_env_result.json: pip install failed: "
    "No matching distribution found for torch==2.1.0+cu118 (see artifacts/build_env.log)."
)


class FakeRun:
    """Writes .sure/runs/<run_id>/ the way run-manager.ts / extension.ts do: run.json, state.json,
    events.jsonl (created, pre_start_state, started, tool_call, tool_result, post_tool_result_state,
    tool_result_repair, finished / session_shutdown) and artifacts/. The checkpoint bookkeeping mirrors
    checkpoints.ts advance() / bumpRetry(): advance clears the unit's retry counter."""

    def __init__(self, runs_root: Path, run_id: str, skill: str, args: str, output_dir: str | None = None) -> None:
        self.dir = runs_root / run_id
        (self.dir / "artifacts").mkdir(parents=True)
        (self.dir / "logs").mkdir()
        self.run_id, self.skill, self.args = run_id, skill, args
        self.registry = UNITS["skills"][skill]
        self.current = self.registry[0]
        self.completed: list[str] = []
        self.retries: dict[str, int] = {}
        self.digests: dict[str, str] = {}
        self.events: list[dict] = []
        self.calls = 0
        self.record: dict = {
            "runId": run_id, "skillName": skill, "command": skill, "status": "pending",
            "cwd": str(runs_root.parents[1]), "packageDir": f"/packages/{skill}", "runDir": str(self.dir),
            "args": args, "startedAt": TS, "updatedAt": TS,
        }
        if output_dir:
            self.record["outputDir"] = output_dir
        self._event("created", dict(self.record))
        self._state_event("pre_start_state", {"phase": self._phase("running"), "message": "loaded", "counters": {}, "checkpoint": self._checkpoint()})
        self.record["status"] = "running"
        self._event("started", {"status": "running"})

    # --- checkpoint shapes -------------------------------------------------------
    def _checkpoint(self) -> dict:
        return {
            "id": "main_flow", "label": "state machine", "resumable": True, "resume_hint": f'Resume at unit "{self.current}".',
            "data": {"currentUnit": self.current, "completedUnits": list(self.completed), "retries": dict(self.retries), "failedArtifactDigests": dict(self.digests)},
        }

    def _phase(self, status: str) -> dict:
        return {"id": self.current, "label": self.current, "status": status}

    def _event(self, kind: str, data: dict | None = None) -> int:
        event: dict = {"type": kind, "timestamp": TS}
        if data is not None:
            event["data"] = data
        self.events.append(event)
        return len(self.events)

    def _state_event(self, kind: str, patch: dict) -> int:
        return self._event(kind, {"patch": patch, "state": {"checkpoint": patch.get("checkpoint")}})

    def event_count(self) -> int:
        return len(self.events)

    # --- tool traffic --------------------------------------------------------------
    def _tool(self, tool: str, payload: dict, is_error: bool = False) -> None:
        self.calls += 1
        call_id = f"call-{self.calls}"
        self._event("tool_call", {"toolName": tool, "toolCallId": call_id, "input": payload})
        self._event("tool_result", {"toolName": tool, "toolCallId": call_id, "isError": is_error})

    def bash(self, command: str, is_error: bool = False) -> None:
        self._tool("bash", {"command": command}, is_error)

    def read(self, path: str) -> None:
        self._tool("read", {"path": path})

    def write(self, path: str, content: str = "{}") -> None:
        self._tool("write", {"path": path, "content": content})

    # --- gate results ------------------------------------------------------------
    def block(self, unit: str, raw_repair: str, shown_repair: str | None = None, exhausted: bool = False, with_diagnostics: bool = True) -> None:
        assert unit == self.current, f"fixture: cannot block {unit} while current is {self.current}"
        self.retries[unit] = self.retries.get(unit, 0) + 1
        attempt = self.retries[unit]
        self.digests[unit] = f"sha-{unit}-{attempt}"
        if exhausted:
            message = f'Gate "{unit}" exhausted {attempt} blocked attempts: gate script failed'
            phase = {"id": "gate", "label": "SURE onboard gate blocked", "status": "blocked"}
            repair_text = f"{raw_repair} Blocked because: gate script failed. After {attempt} consecutive blocked attempts, /sure_onboard still cannot produce a valid artifact for unit \"{unit}\"."
        else:
            message = f'Gate "{unit}" blocked (attempt {attempt}): gate script failed'
            phase = self._phase("blocked")
            repair_text = raw_repair
        diagnostics = [{"severity": "error", "message": message if exhausted else "gate script failed", "repair": repair_text}]
        patch = {"phase": phase, "message": message, "counters": {"gate_blocks": attempt}, "checkpoint": self._checkpoint()}
        if with_diagnostics:
            patch["diagnostics"] = diagnostics
        self._state_event("post_tool_result_state", patch)
        top = shown_repair if shown_repair is not None else repair_text
        self._event("tool_result_repair", {"ok": False, "message": message, "repair": top, "diagnostics": diagnostics if with_diagnostics else [], "state_patch": patch})
        self.record["lastRepair"] = top

    def pass_unit(self, unit: str) -> None:
        assert unit == self.current, f"fixture: cannot pass {unit} while current is {self.current}"
        self.completed.append(unit)
        self.retries.pop(unit, None)
        self.digests.pop(unit, None)
        idx = self.registry.index(unit)
        if idx + 1 < len(self.registry):
            self.current = self.registry[idx + 1]
        patch = {"phase": self._phase("running"), "message": f'Advanced to unit "{self.current}".', "counters": {}, "checkpoint": self._checkpoint()}
        self._state_event("post_tool_result_state", patch)

    def pass_through(self, until: str) -> None:
        """Pass every unit up to (not including) `until`."""
        while self.current != until:
            self.pass_unit(self.current)

    def finish(self, status: str, error_summary: str | None = None) -> None:
        self.record["status"] = status
        self.record["finishedAt"] = TS
        self.record.pop("lastRepair", None)  # the "finished" writeRecord clears lastRepair
        if error_summary:
            self.record["errorSummary"] = error_summary
        self._event("finished", {"finish": {"status": status}, "manifestPath": "artifacts/manifest.json"})

    def shutdown(self, status: str = "failed") -> None:
        self.record["status"] = status
        self.record["finishedAt"] = TS
        self._event("session_shutdown", {"status": status})

    # --- files ---------------------------------------------------------------------
    def artifact(self, name: str, obj: object) -> Path:
        path = self.dir / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
        return path

    def file(self, relpath: str, data: bytes) -> Path:
        path = self.dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def save(self) -> Path:
        (self.dir / "run.json").write_text(json.dumps(self.record, indent=2), encoding="utf-8")
        (self.dir / "state.json").write_text(json.dumps({"checkpoint": self._checkpoint()}, indent=2), encoding="utf-8")
        (self.dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.events), encoding="utf-8")
        return self.dir


class DigestTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name) / "repo"
        self.runs_root = self.repo_root / ".sure" / "runs"
        self.runs_root.mkdir(parents=True)
        self.memory_root = paths.memory_root(self.repo_root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build(self, run: FakeRun, **kwargs) -> dict:
        run.save()
        kwargs.setdefault("cutoff", None)
        kwargs.setdefault("mark_passed", None)
        kwargs.setdefault("config", CONFIG)
        kwargs.setdefault("units", UNITS)
        kwargs.setdefault("log_paths", LOG_PATHS)
        return digest.build_run_digest(run.dir, self.repo_root, **kwargs)

    @staticmethod
    def unit(d: dict, unit_id: str) -> dict:
        for row in d["units"]:
            if row["id"] == unit_id:
                return row
        raise AssertionError(f"unit {unit_id} not in digest: {[u['id'] for u in d['units']]}")

    def onboard_run(self, run_id: str = "20260818-120000-aaaa0001", args: str = "model_id=openai/whisper-large-v3 model_input_path=sure/handoffs/whisper/MODEL_INPUT.yaml") -> FakeRun:
        run = FakeRun(self.runs_root, run_id, "sure_onboard", args)
        run.artifact("model_input_resolved.json", {"model_id": "openai/whisper-large-v3", "model_name": "openai__whisper-large-v3", "model_dir": str(self.repo_root / "sure" / "models" / "openai__whisper-large-v3")})
        return run

    def trans_run(self, run_id: str = "20260818-120000-tttt0001",
                  args: str = "dockerfile=/srv/build/Dockerfile model=/srv/weights/whisper model_name=openai__whisper-large-v3 task_type=asr") -> FakeRun:
        run = FakeRun(self.runs_root, run_id, "sure_trans", args)
        run.artifact("trans_input_resolved.json", {"schema": "sure.trans.input.v2", "model_name": "openai__whisper-large-v3",
                                                   "model_dir": str(self.repo_root / "sure" / "models" / "openai__whisper-large-v3")})
        return run


class StripOutputDirTests(unittest.TestCase):
    def test_removes_key_value_form(self) -> None:
        self.assertEqual(digest.strip_output_dir("model=demo output_dir=/tmp/out datasets=a,b"), "model=demo datasets=a,b")

    def test_removes_flag_value_form(self) -> None:
        self.assertEqual(digest.strip_output_dir("model=demo --output_dir /tmp/out max_samples=3"), "model=demo max_samples=3")
        self.assertEqual(digest.strip_output_dir("-output_dir /tmp/out model=demo"), "model=demo")
        self.assertEqual(digest.strip_output_dir("output_dir /tmp/out model=demo"), "model=demo")

    def test_flag_without_value_drops_only_the_flag(self) -> None:
        self.assertEqual(digest.strip_output_dir("model=demo --output_dir --strict"), "model=demo --strict")
        self.assertEqual(digest.strip_output_dir("model=demo --output_dir"), "model=demo")

    def test_keeps_other_tokens_and_order(self) -> None:
        self.assertEqual(digest.strip_output_dir("  a=1   b=2  "), "a=1 b=2")
        self.assertEqual(digest.strip_output_dir(""), "")
        self.assertEqual(digest.strip_output_dir("output_dir=/x"), "")


class MaskArgValuesTests(unittest.TestCase):
    def test_absolute_values_are_masked_one_by_one(self) -> None:
        self.assertEqual(digest.mask_arg_values("model=demo datasets=/data/libri,/data/aishell"),
                         "model=demo datasets=<path>,<path>")
        self.assertEqual(digest.mask_arg_values("dockerfile=/srv/build/Dockerfile model=/srv/weights/whisper"),
                         "dockerfile=<path> model=<path>")
        self.assertEqual(digest.mask_arg_values("--model_input_path /srv/inputs/x.yaml"), "--model_input_path <path>")

    def test_url_values_are_masked(self) -> None:
        self.assertEqual(digest.mask_arg_values("hf_endpoint=https://mirror.internal.example/hf"), "hf_endpoint=<url>")
        self.assertEqual(digest.mask_arg_values("url=https://modelscope.cn/models/iic/SenseVoiceSmall"), "url=<url>")

    def test_quoted_nested_and_home_relative_values_are_masked(self) -> None:
        self.assertEqual(digest.mask_arg_values('model="/srv/weights/whisper"'), "model=<path>")
        self.assertEqual(digest.mask_arg_values("env=PYTHONPATH=/srv/site/lib"), "env=PYTHONPATH=<path>")
        self.assertEqual(digest.mask_arg_values("model=~/weights/whisper"), "model=<path>")
        self.assertEqual(digest.mask_arg_values("proxy='https://mirror.internal.example'"), "proxy=<url>")

    def test_relative_enum_and_number_values_are_left_alone(self) -> None:
        args = "model_name=openai__whisper task_type=asr max_retries=3 model_input_path=sure/handoffs/x.yaml --strict"
        self.assertEqual(digest.mask_arg_values(args), args)
        self.assertEqual(digest.mask_arg_values(""), "")


class StripMemoryBlockTests(unittest.TestCase):
    def test_removes_appended_block(self) -> None:
        text = f"{RAW_BUILD_ENV_REPAIR}\n\n{MEMORY_BLOCK}"
        self.assertEqual(digest.strip_memory_block(text, HEADER), RAW_BUILD_ENV_REPAIR)

    def test_removes_prepended_block_and_keeps_following_text(self) -> None:
        text = f"{MEMORY_BLOCK}\n\n{RAW_BUILD_ENV_REPAIR}"
        self.assertEqual(digest.strip_memory_block(text, HEADER), RAW_BUILD_ENV_REPAIR)

    def test_keeps_text_before_header_on_same_line_and_strips_two_blocks(self) -> None:
        text = f"first {MEMORY_BLOCK}\n\nsecond\n\n{MEMORY_BLOCK}"
        self.assertEqual(digest.strip_memory_block(text, HEADER), "first\n\nsecond")

    def test_block_ends_at_the_first_blank_line(self) -> None:
        # match.ts writes the block as one paragraph; a line glued to it without a blank line is part of it
        text = f"raw\n\n{MEMORY_BLOCK}\nglued line\n\nkept"
        self.assertEqual(digest.strip_memory_block(text, HEADER), "raw\n\nkept")

    def test_text_without_header_is_unchanged(self) -> None:
        self.assertEqual(digest.strip_memory_block(RAW_BUILD_ENV_REPAIR, HEADER), RAW_BUILD_ENV_REPAIR)
        self.assertEqual(digest.strip_memory_block("", HEADER), "")


class ReadLogTailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "build.log"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_splits_on_bare_cr_and_crlf(self) -> None:
        self.path.write_bytes(b"step 1\rstep 2\r\nstep 3\nstep 4")
        self.assertEqual(digest.read_log_tail(self.path, 30, 300, 65536), ["step 1", "step 2", "step 3", "step 4"])

    def test_seek_drops_torn_first_line_and_keeps_last_lines(self) -> None:
        lines = [f"line {i:04d} " + "x" * 50 for i in range(200)]
        self.path.write_bytes("\r".join(lines).encode("utf-8") + b"\r")
        tail = digest.read_log_tail(self.path, 30, 300, 1000)
        self.assertEqual(len(tail), 16)  # 1000 bytes / 61 bytes per line, torn first line dropped
        self.assertEqual(tail[-1], lines[-1])
        self.assertTrue(all(t.startswith("line ") for t in tail))

    def test_max_lines_and_max_line_chars(self) -> None:
        self.path.write_bytes(b"\n".join(b"row%d " % i + b"y" * 400 for i in range(40)))
        tail = digest.read_log_tail(self.path, 3, 10, 65536)
        self.assertEqual(tail, ["row37 yyyy", "row38 yyyy", "row39 yyyy"])

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(digest.read_log_tail(self.path, 30, 300, 65536), [])


class ResolveTargetTests(DigestTestBase):
    def test_onboard_reads_model_id_from_resolved_input(self) -> None:
        run = self.onboard_run(args="model_id=wrong/one")
        self.assertEqual(digest.resolve_target(run.dir, "sure_onboard", run.args), {"kind": "model", "id": "openai/whisper-large-v3"})

    def test_eval_reads_user_input_model_and_ignores_runtime_paths(self) -> None:
        run = FakeRun(self.runs_root, "20260818-120000-eeee0001", "sure_eval", "model=demo-model datasets=librispeech")
        run.artifact("eval_input_resolved.json", {"user_input": {"model": "demo-model", "datasets": ["librispeech"]}, "runtime": {"run_dir": "/tmp/out-dir-xyz"}})
        self.assertEqual(digest.resolve_target(run.dir, "sure_eval", run.args), {"kind": "eval", "id": "demo-model"})

    def test_falls_back_to_args_when_artifact_missing(self) -> None:
        run = FakeRun(self.runs_root, "20260818-120000-eeee0002", "sure_eval", "model=arg-model output_dir=/tmp/o")
        self.assertEqual(digest.resolve_target(run.dir, "sure_eval", run.args)["id"], "arg-model")
        run2 = FakeRun(self.runs_root, "20260818-120000-aaaa0002", "sure_onboard", "--model_id arg/onboard")
        self.assertEqual(digest.resolve_target(run2.dir, "sure_onboard", run2.args)["id"], "arg/onboard")
        self.assertEqual(digest.resolve_target(run2.dir, "sure_onboard", "")["id"], "")

    def test_trans_reads_model_name_and_never_falls_back_to_the_model_path(self) -> None:
        run = self.trans_run()
        self.assertEqual(digest.resolve_target(run.dir, "sure_trans", run.args),
                         {"kind": "model", "id": "openai__whisper-large-v3"})
        # /sure_trans model= is the host path to the weights: without the artifact the id comes
        # from model_name, and with neither it stays empty rather than becoming that path.
        (run.dir / "artifacts" / "trans_input_resolved.json").unlink()
        self.assertEqual(digest.resolve_target(run.dir, "sure_trans", run.args)["id"], "openai__whisper-large-v3")
        self.assertEqual(digest.resolve_target(run.dir, "sure_trans", "model=/srv/weights/whisper")["id"], "")

    def test_feed_reads_the_selected_model_id_from_feed_report(self) -> None:
        run = FakeRun(self.runs_root, "20260818-120000-ffff0001", "sure_feed", "url=https://modelscope.cn/models/iic/SenseVoiceSmall")
        self.assertEqual(digest.resolve_target(run.dir, "sure_feed", run.args), {"kind": "model", "id": ""})
        run.artifact("feed_report.json", {"status": "no_selection", "selected": None})
        self.assertEqual(digest.resolve_target(run.dir, "sure_feed", run.args)["id"], "")
        run.artifact("feed_report.json", {"status": "ready_for_onboard", "selected": {"model_id": "iic/SenseVoiceSmall"}})
        self.assertEqual(digest.resolve_target(run.dir, "sure_feed", run.args)["id"], "iic/SenseVoiceSmall")


class ProductDirTests(DigestTestBase):
    def test_feed_product_dir_is_the_handoff_dir_and_trans_has_none(self) -> None:
        run = FakeRun(self.runs_root, "20260818-120000-ffff0002", "sure_feed", "query=sensevoice")
        handoff = self.repo_root / "sure" / "handoffs" / "sense-voice-small"
        run.artifact("feed_report.json", {"selected": {"model_id": "iic/SenseVoiceSmall"}, "handoff": {"handoff_dir": str(handoff)}})
        self.assertEqual(digest._product_dir(run.dir, "sure_feed"), handoff)
        run.artifact("feed_report.json", {"selected": None, "handoff": None})
        self.assertIsNone(digest._product_dir(run.dir, "sure_feed"))
        # sure_trans copies its logs into model_dir only in its last unit, so it registers none.
        self.assertIsNone(digest._product_dir(self.trans_run(run_id="20260818-120000-tttt0002").dir, "sure_trans"))


class BuildDigestTests(DigestTestBase):
    def test_units_outcomes_attempts_and_repairs_from_events(self) -> None:
        run = self.onboard_run()
        run.pass_through("build_env")
        run.write(".sure/runs/x/artifacts/build_env_result.json")
        run.block("build_env", RAW_BUILD_ENV_REPAIR)
        run.bash("pip install torch==2.1.0 --index-url https://download.pytorch.org/whl/cu118")
        run.block("build_env", "Gate script scripts/check_build_env.py rejected build_env_result.json: import torch failed.")
        run.bash("pip install torch==2.1.0+cu118 --extra-index-url https://download.pytorch.org/whl/cu118")
        run.pass_unit("build_env")
        d = self.build(run)
        self.assertEqual(d["schema"], "sure.memory.run_digest.v1")
        self.assertEqual(d["run"]["run_id"], run.run_id)
        self.assertEqual(d["run"]["skill"], "sure_onboard")
        self.assertEqual(d["run"]["status_so_far"], "running")
        self.assertEqual([u["id"] for u in d["units"]], ONBOARD[: ONBOARD.index("fetch_weights") + 1])
        plan = self.unit(d, "plan")
        self.assertEqual((plan["outcome"], plan["attempts"], plan["repairs"], plan["fix_window"], plan["last_commands"]), ("passed", 1, [], [], []))
        build_env = self.unit(d, "build_env")
        self.assertEqual(build_env["outcome"], "passed")
        self.assertEqual(build_env["attempts"], 3)
        self.assertEqual([r["attempt"] for r in build_env["repairs"]], [1, 2])
        self.assertEqual(build_env["repairs"][0]["text"], RAW_BUILD_ENV_REPAIR)
        current = self.unit(d, "fetch_weights")
        self.assertEqual((current["outcome"], current["attempts"]), ("current", 0))
        self.assertNotIn("log_tail", current)

    def test_fix_window_only_for_fail_then_pass_units(self) -> None:
        run = self.onboard_run()
        run.bash("cat sure/handoffs/whisper/MODEL_INPUT.yaml")  # before any block: never in a window
        run.pass_through("build_env")
        run.block("build_env", RAW_BUILD_ENV_REPAIR)
        for i in range(12):
            run.bash(f"pip install attempt-{i}")
        run.write(".sure/runs/x/artifacts/build_env_result.json")
        run.pass_unit("build_env")
        run.bash("ls after-pass")
        run.pass_unit("fetch_weights")
        d = self.build(run)
        window = self.unit(d, "build_env")["fix_window"]
        self.assertEqual(len(window), CONFIG["digest_limits"]["fix_window_commands"])
        self.assertEqual(window[0], {"tool": "bash", "command": "pip install attempt-3"})
        self.assertEqual(window[-1], {"tool": "write", "command": ".sure/runs/x/artifacts/build_env_result.json"})
        self.assertEqual(self.unit(d, "fetch_weights")["fix_window"], [])
        self.assertEqual(self.unit(d, "plan")["fix_window"], [])
        self.assertEqual(self.unit(d, "build_env")["last_commands"], [])

    def test_last_commands_only_for_terminal_failed_units(self) -> None:
        run = self.onboard_run()
        run.pass_through("validate_import")
        for i in range(14):
            run.bash(f"python -c 'import step{i}'")
        run.block("validate_import", "import failed: No module named 'torchaudio'")
        run.bash("pip install torchaudio")
        run.block("validate_import", "import failed: No module named 'torchaudio'", exhausted=False)
        run.read(".sure/runs/x/artifacts/import_execution.log")
        run.block("validate_import", "import failed: undefined symbol: cudaGetErrorString", exhausted=True)
        d = self.build(run)
        failed = self.unit(d, "validate_import")
        self.assertEqual(failed["outcome"], "failed")
        self.assertEqual(failed["attempts"], 3)
        self.assertEqual(len(failed["last_commands"]), CONFIG["digest_limits"]["last_commands"])
        self.assertEqual(failed["last_commands"][-1], {"tool": "read", "command": ".sure/runs/x/artifacts/import_execution.log"})
        self.assertEqual(failed["last_commands"][-2], {"tool": "bash", "command": "pip install torchaudio"})
        self.assertEqual(failed["fix_window"], [])
        self.assertIn("Blocked because", failed["repairs"][-1]["text"])
        for passed in ("plan", "build_env", "validate_env_compat"):
            self.assertEqual(self.unit(d, passed)["last_commands"], [])
        self.assertEqual(d["units"][-1]["id"], "validate_import")

    def test_stuck_unit_becomes_failed_on_terminal_run_signal_or_finish_status(self) -> None:
        run = self.onboard_run()
        run.pass_through("build_env")
        run.block("build_env", RAW_BUILD_ENV_REPAIR)
        run.bash("pip install torch")
        d = self.build(run)
        self.assertEqual(self.unit(d, "build_env")["outcome"], "current")
        self.assertEqual(self.unit(d, "build_env")["last_commands"], [])
        d = self.build(run, finish_status="failed")
        self.assertEqual(self.unit(d, "build_env")["outcome"], "failed")
        self.assertEqual(d["run"]["status_so_far"], "failed")
        self.assertEqual(len(self.unit(d, "build_env")["last_commands"]), 1)
        run.shutdown("failed")
        d = self.build(run)
        self.assertEqual(self.unit(d, "build_env")["outcome"], "failed")
        self.assertEqual(d["run"]["status_so_far"], "failed")

    def test_current_unit_without_blocks_stays_current_after_shutdown(self) -> None:
        run = self.onboard_run()
        run.pass_through("discover")
        run.shutdown("cancelled")
        d = self.build(run)
        self.assertEqual(self.unit(d, "discover")["outcome"], "current")
        self.assertEqual(d["run"]["status_so_far"], "cancelled")

    def test_cutoff_ignores_later_events(self) -> None:
        run = self.onboard_run()
        run.pass_through("build_env")
        run.block("build_env", RAW_BUILD_ENV_REPAIR)
        cutoff = run.event_count()
        run.bash("pip install torch")
        run.pass_unit("build_env")
        run.pass_unit("fetch_weights")
        d = self.build(run, cutoff=cutoff)
        self.assertEqual(d["run"]["cutoff"], cutoff)
        self.assertEqual(self.unit(d, "build_env")["outcome"], "current")
        self.assertEqual(self.unit(d, "build_env")["attempts"], 1)
        self.assertEqual(d["units"][-1]["id"], "build_env")
        full = self.build(run)
        self.assertEqual(full["run"]["cutoff"], run.event_count())
        self.assertEqual(self.unit(full, "build_env")["outcome"], "passed")

    def test_torn_last_line_is_neither_counted_nor_parsed(self) -> None:
        # §1.13: a line is a "\n"-terminated record; a record still being written (valid JSON, no newline yet)
        # is not counted toward cutoff and its content is ignored, so digest and hooks readEventCount agree
        run = self.onboard_run()
        run.pass_through("build_env")
        run.block("build_env", RAW_BUILD_ENV_REPAIR)
        run.save()
        complete = run.event_count()
        torn = json.dumps({"type": "session_shutdown", "timestamp": TS, "data": {"status": "failed"}})
        with (run.dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(torn)  # no trailing "\n"
        d = digest.build_run_digest(run.dir, self.repo_root, cutoff=None, mark_passed=None, config=CONFIG, units=UNITS, log_paths=LOG_PATHS)
        self.assertEqual(d["run"]["cutoff"], complete)
        self.assertEqual(d["run"]["status_so_far"], "running")  # the torn shutdown record was not read
        self.assertEqual(self.unit(d, "build_env")["outcome"], "current")

    def test_read_events_stops_at_the_cutoff_without_loading_the_file(self) -> None:
        # The digest is rebuilt on every unit pass, pre_finish and on_error, and production
        # events.jsonl runs to hundreds of MB: reading past the cutoff costs multiples of the file
        # size for lines that are then thrown away. Peak measured with tracemalloc, as in proposals.
        run_dir = self.runs_root / "20260818-120000-bulk0001"
        run_dir.mkdir(parents=True)
        head = "".join(json.dumps({"type": "tool_call", "timestamp": TS, "data": {"n": i}}) + "\n" for i in range(3))
        junk = (json.dumps({"type": "tool_result", "timestamp": TS, "data": {"pad": "x" * 60}}) + "\n").encode()
        block = junk * 1000
        with (run_dir / "events.jsonl").open("wb") as handle:
            handle.write(head.encode())
            for _ in range(200):
                handle.write(block)
        size = (run_dir / "events.jsonl").stat().st_size
        tracemalloc.start()
        try:
            events, limit = digest._read_events(run_dir, 3)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(([e["data"]["n"] for e in events], limit), ([0, 1, 2], 3))
        self.assertLess(peak, 2 * 1024 * 1024, f"peak {peak} on a {size} byte events.jsonl")

    def test_mark_passed_marks_unit_passed_and_moves_current(self) -> None:
        run = self.onboard_run()
        run.pass_through("verdict")
        run.block("verdict", "verdict.json: decision must be one of ...")
        run.bash("python scripts/check_verdict.py --help")
        run.write(".sure/runs/x/artifacts/verdict.json")
        # the advance for verdict is not in events yet: the hook counts cutoff before it lands
        d = self.build(run, mark_passed="verdict")
        verdict = self.unit(d, "verdict")
        self.assertEqual((verdict["outcome"], verdict["attempts"]), ("passed", 2))
        self.assertEqual([c["command"] for c in verdict["fix_window"]], ["python scripts/check_verdict.py --help", ".sure/runs/x/artifacts/verdict.json"])
        self.assertEqual(d["units"][-1]["id"], "extract_lessons")
        self.assertEqual((d["units"][-1]["outcome"], d["units"][-1]["attempts"]), ("current", 0))
        without = self.build(run)
        self.assertEqual(self.unit(without, "verdict")["outcome"], "current")

    def test_missing_events_without_a_cutoff_yields_cutoff_zero_and_first_unit_current(self) -> None:
        run = self.onboard_run()
        run.save()
        (run.dir / "events.jsonl").unlink()
        d = digest.build_run_digest(run.dir, self.repo_root, cutoff=None, mark_passed=None, config=CONFIG, units=UNITS, log_paths=LOG_PATHS)
        self.assertNotIn("error", d)
        self.assertEqual(d["run"]["cutoff"], 0)
        self.assertEqual([(u["id"], u["outcome"]) for u in d["units"]], [("load_model_input", "current")])
        self.assertEqual(d["run"]["skill"], "sure_onboard")  # from run.json

    def test_fewer_lines_than_the_hook_counted_is_an_error_digest(self) -> None:
        # The hook counts the lines itself and passes them as --cutoff, so reading fewer means the
        # file was truncated or could not be read. Returning ([], 0) there is indistinguishable
        # from an empty file and lets the digest invent a run history.
        run = self.onboard_run()
        run.save()
        (run.dir / "events.jsonl").unlink()
        d = digest.build_run_digest(run.dir, self.repo_root, cutoff=812, mark_passed=None, config=CONFIG, units=UNITS, log_paths=LOG_PATHS)
        self.assertEqual(set(d), {"schema", "error"})
        self.assertIn("812", d["error"])
        for absolute in (run.dir, self.repo_root):
            self.assertNotIn(json.dumps(str(absolute))[1:-1], json.dumps(d))

    def test_unreadable_events_do_not_fabricate_a_history_for_the_production_call(self) -> None:
        # onEnterExtractLessons always calls with --cutoff <its own count> --mark-passed <unit>;
        # the fabricated digest claimed every unit before verdict was "skipped" while verdict
        # "passed", a state the state machine cannot produce.
        run = self.onboard_run()
        run.pass_through("verdict")
        run.save()
        cutoff = run.event_count()
        (run.dir / "events.jsonl").unlink()
        d = digest.build_run_digest(run.dir, self.repo_root, cutoff=cutoff, mark_passed="verdict", config=CONFIG, units=UNITS, log_paths=LOG_PATHS)
        self.assertEqual(set(d), {"schema", "error"})
        self.assertNotIn("units", d)

    def test_truncated_events_are_an_error_digest(self) -> None:
        run = self.onboard_run()
        run.pass_through("build_env")
        run.save()
        cutoff = run.event_count()
        lines = (run.dir / "events.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
        (run.dir / "events.jsonl").write_text("".join(lines[:-2]), encoding="utf-8")
        d = digest.build_run_digest(run.dir, self.repo_root, cutoff=cutoff, mark_passed=None, config=CONFIG, units=UNITS, log_paths=LOG_PATHS)
        self.assertEqual(set(d), {"schema", "error"})
        # more lines than the hook counted is normal: the run keeps appending while we read
        d = digest.build_run_digest(run.dir, self.repo_root, cutoff=cutoff - 4, mark_passed=None, config=CONFIG, units=UNITS, log_paths=LOG_PATHS)
        self.assertNotIn("error", d)

    def test_skill_override_when_run_json_and_events_are_missing(self) -> None:
        run_dir = self.runs_root / "20260818-120000-bare0001"
        (run_dir / "artifacts").mkdir(parents=True)
        d = digest.build_run_digest(run_dir, self.repo_root, cutoff=None, mark_passed=None, config=CONFIG, units=UNITS, log_paths=LOG_PATHS)
        self.assertEqual((d["run"]["skill"], d["units"], d["run"]["target"]["id"]), (None, [], ""))
        d = digest.build_run_digest(run_dir, self.repo_root, cutoff=None, mark_passed=None, config=CONFIG, units=UNITS, log_paths=LOG_PATHS, skill="sure_eval")
        self.assertEqual(d["run"]["skill"], "sure_eval")
        self.assertEqual([(u["id"], u["outcome"]) for u in d["units"]], [("task_classification", "current")])

    def test_repairs_prefer_diagnostics_raw_text_over_shown_repair(self) -> None:
        run = self.onboard_run()
        run.pass_through("build_env")
        run.block("build_env", RAW_BUILD_ENV_REPAIR, shown_repair=f"{RAW_BUILD_ENV_REPAIR}\n\n{MEMORY_BLOCK}")
        d = self.build(run)
        self.assertEqual(self.unit(d, "build_env")["repairs"], [{"attempt": 1, "text": RAW_BUILD_ENV_REPAIR}])
        self.assertNotIn(HEADER, json.dumps(d))

    def test_repairs_strip_memory_block_by_header_when_no_diagnostics(self) -> None:
        run = self.onboard_run()
        run.pass_through("build_env")
        run.block("build_env", RAW_BUILD_ENV_REPAIR, shown_repair=f"{RAW_BUILD_ENV_REPAIR}\n\n{MEMORY_BLOCK}", with_diagnostics=False)
        d = self.build(run)
        self.assertEqual(self.unit(d, "build_env")["repairs"], [{"attempt": 1, "text": RAW_BUILD_ENV_REPAIR}])

    def test_repair_text_clipped_head_and_tail(self) -> None:
        run = self.onboard_run()
        run.pass_through("build_env")
        long_repair = "H" * 500 + "M" * 500 + "T" * 500
        run.block("build_env", long_repair)
        d = self.build(run)
        text = self.unit(d, "build_env")["repairs"][0]["text"]
        limits = CONFIG["digest_limits"]
        self.assertTrue(text.startswith("H" * limits["repair_head_chars"]))
        self.assertTrue(text.endswith("T" * limits["repair_tail_chars"]))
        self.assertIn("chars omitted", text)
        self.assertLess(len(text), limits["repair_head_chars"] + limits["repair_tail_chars"] + 40)

    def test_non_bash_tool_calls_record_only_path_and_commands_are_truncated(self) -> None:
        run = self.onboard_run()
        run.pass_through("build_env")
        run.block("build_env", RAW_BUILD_ENV_REPAIR)
        run.read("sure/skills/sure_onboard/references/memory/bad_cases/no-kernel-image.md")
        run.write(".sure/runs/x/artifacts/build_env_result.json", content="{\"secret\": \"never copied\"}")
        run.bash("echo " + "y" * 400)
        run.pass_unit("build_env")
        d = self.build(run)
        window = self.unit(d, "build_env")["fix_window"]
        self.assertEqual(window[0], {"tool": "read", "command": "sure/skills/sure_onboard/references/memory/bad_cases/no-kernel-image.md"})
        self.assertEqual(window[1], {"tool": "write", "command": ".sure/runs/x/artifacts/build_env_result.json"})
        self.assertEqual(len(window[2]["command"]), CONFIG["digest_limits"]["command_chars"])
        self.assertNotIn("never copied", json.dumps(d))

    def test_tool_errors_counted(self) -> None:
        run = self.onboard_run()
        run.bash("false", is_error=True)
        run.bash("true")
        run.bash("exit 2", is_error=True)
        d = self.build(run)
        self.assertEqual(d["tool_errors"], 2)

    def test_log_tail_for_failed_unit_records_template_not_absolute_path(self) -> None:
        run = self.onboard_run()
        run.pass_through("build_env")
        lines = [f"[{i:05d}] Building wheel for flash-attn ... " + "=" * 40 for i in range(2000)]
        run.file("artifacts/build_env.log", "\r".join(lines).encode("utf-8") + b"\rerror: no kernel image is available for execution on the device\r")
        run.block("build_env", RAW_BUILD_ENV_REPAIR, exhausted=True)
        d = self.build(run)
        tail = self.unit(d, "build_env")["log_tail"]
        self.assertEqual(tail["path"], "{run_dir}/artifacts/build_env.log")
        self.assertEqual(len(tail["lines"]), CONFIG["digest_limits"]["log_tail_lines"])
        self.assertEqual(tail["lines"][-1], "error: no kernel image is available for execution on the device")
        self.assertEqual(tail["lines"][-2], lines[-1])
        blob = json.dumps(d)
        for absolute in (run.dir, self.repo_root):  # compare the JSON-escaped spelling so Windows backslashes count too
            self.assertNotIn(json.dumps(str(absolute))[1:-1], blob)

    def test_log_tail_from_artifact_log_path_field(self) -> None:
        log_paths = {"schema": "sure.memory.log_paths.v1", "sure_onboard": {"build_env": ["artifact:build_env_result.json", "{run_dir}/artifacts/build_env.log"]}}
        run = self.onboard_run()
        run.pass_through("build_env")
        run.file("artifacts/build_env.log", b"table log\n")
        model_dir = self.repo_root / "sure" / "models" / "openai__whisper-large-v3"
        (model_dir / "artifacts").mkdir(parents=True)
        (model_dir / "artifacts" / "custom_build.log").write_bytes(b"product log line 1\nproduct log line 2\n")
        run.artifact("build_env_result.json", {"status": "failed", "log_path": "artifacts/custom_build.log"})
        run.block("build_env", RAW_BUILD_ENV_REPAIR, exhausted=True)
        d = self.build(run, log_paths=log_paths)
        tail = self.unit(d, "build_env")["log_tail"]
        self.assertEqual(tail, {"path": "artifact:build_env_result.json", "lines": ["product log line 1", "product log line 2"]})
        # an absolute log_path is used as is; a bare relative one lives under run artifacts
        absolute = self.repo_root / "elsewhere.log"
        absolute.write_bytes(b"abs line\n")
        run.artifact("build_env_result.json", {"status": "failed", "log_path": str(absolute)})
        self.assertEqual(self.build(run, log_paths=log_paths)["units"][-1]["log_tail"]["lines"], ["abs line"])
        run.file("artifacts/rel.log", b"rel line\n")
        run.artifact("build_env_result.json", {"status": "failed", "log_path": "rel.log"})
        self.assertEqual(self.build(run, log_paths=log_paths)["units"][-1]["log_tail"]["lines"], ["rel line"])
        # no log_path field: falls through to the next template
        run.artifact("build_env_result.json", {"status": "failed"})
        self.assertEqual(self.build(run, log_paths=log_paths)["units"][-1]["log_tail"], {"path": "{run_dir}/artifacts/build_env.log", "lines": ["table log"]})

    def test_trans_validate_log_tail_from_the_registered_template(self) -> None:
        run = self.trans_run()
        run.pass_through("validate_import")
        run.file("artifacts/import_execution.log", b"ImportError: No module named 'sure_adapter'\n")
        run.block("validate_import", "import_result.json: status must be passed", exhausted=True)
        self.assertEqual(self.unit(self.build(run), "validate_import")["log_tail"],
                         {"path": "{run_dir}/artifacts/import_execution.log", "lines": ["ImportError: No module named 'sure_adapter'"]})

    def test_trans_validate_log_tail_from_the_artifact_log_path(self) -> None:
        run = self.trans_run(run_id="20260818-120000-tttt0003")
        run.pass_through("validate_infer")
        run.artifact("infer_result.json", {"status": "failed", "log_path": "infer_elsewhere.log"})
        run.file("artifacts/infer_elsewhere.log", b"inference crashed\n")
        run.block("validate_infer", "infer_result.json: status must be passed", exhausted=True)
        self.assertEqual(self.unit(self.build(run), "validate_infer")["log_tail"],
                         {"path": "artifact:infer_result.json", "lines": ["inference crashed"]})

    def test_trans_image_units_never_read_a_log_path_from_the_artifact(self) -> None:
        # run_docker_build.py writes source_image_log_path and the registry gate nests its smoke
        # log under post_pull_smoke, so neither unit can use the `artifact:` form.
        run = self.trans_run(run_id="20260818-120000-tttt0004")
        run.pass_through("build_source_image")
        run.artifact("source_image_result.json", {"status": "failed", "source_image_log_path": "/nowhere/ignored.log"})
        run.file("artifacts/source_image_build.log", b"docker build exited 1\n")
        run.block("build_source_image", "source_image_result.json: status must be passed", exhausted=True)
        self.assertEqual(self.unit(self.build(run), "build_source_image")["log_tail"],
                         {"path": "{run_dir}/artifacts/source_image_build.log", "lines": ["docker build exited 1"]})

    def test_trans_digest_keeps_host_paths_out_of_args_and_target(self) -> None:
        run = self.trans_run(run_id="20260818-120000-tttt0005")
        run.pass_through("build_source_image")
        run.block("build_source_image", "source_image_result.json: status must be passed", exhausted=True)
        d = self.build(run)
        self.assertEqual(d["run"]["args"], "dockerfile=<path> model=<path> model_name=openai__whisper-large-v3 task_type=asr")
        self.assertEqual(d["run"]["target"], {"kind": "model", "id": "openai__whisper-large-v3"})

    def test_log_tail_absent_for_passed_units_and_when_no_log_exists(self) -> None:
        run = self.onboard_run()
        run.pass_through("build_env")
        run.file("artifacts/build_env.log", b"fine\n")
        run.block("build_env", RAW_BUILD_ENV_REPAIR)
        run.pass_unit("build_env")
        run.block("fetch_weights", "weights_manifest.json missing sha256", exhausted=True)
        d = self.build(run)
        self.assertNotIn("log_tail", self.unit(d, "build_env"))
        self.assertNotIn("log_tail", self.unit(d, "fetch_weights"))  # no log registered for fetch_weights

    def test_output_dir_never_appears_in_digest(self) -> None:
        out_dir = "/tmp/out-dir-xyz"
        run = FakeRun(self.runs_root, "20260818-120000-eeee0003", "sure_eval", f"model=demo-model datasets=librispeech output_dir={out_dir}", output_dir=out_dir)
        run.artifact("eval_input_resolved.json", {
            "schema": "sure.eval.input_resolved.v1",
            "user_input": {"model": "demo-model", "datasets": ["librispeech"]},
            "runtime": {"run_dir": out_dir, "run_id": "main_agent_demo-model_20260818"},
            "expected_outputs": {"report_jsonl": f"{out_dir}/report.jsonl"},
        })
        run.pass_through("smoke_test")
        run.file("local_logs/smoke_test.log", b"smoke failed: CUDA error: no kernel image is available\n")
        run.block("smoke_test", "smoke_test_result.json: status must be passed", exhausted=True)
        d = self.build(run)
        blob = json.dumps(d)
        self.assertNotIn(out_dir, blob)
        self.assertNotIn("output_dir", blob)
        self.assertEqual(d["run"]["args"], "model=demo-model datasets=librispeech")
        self.assertEqual(d["run"]["target"], {"kind": "eval", "id": "demo-model"})
        self.assertEqual(self.unit(d, "smoke_test")["log_tail"]["path"], "{run_dir}/local_logs/smoke_test.log")

    def test_output_dir_quoted_by_a_gate_is_masked(self) -> None:
        out_dir = str(Path(self.tmp.name) / "spelled-out")
        run = FakeRun(self.runs_root, "20260818-120000-eeee0011", "sure_eval", f"model=demo-model output_dir={out_dir}", output_dir=out_dir)
        run.pass_through("smoke_test")
        run.file("local_logs/smoke_test.log", f"smoke failed, see {out_dir}/artifacts/smoke.log\n".encode("utf-8"))
        run.block("smoke_test", f"smoke_test_result.json missing, see {out_dir}/artifacts/smoke.log", exhausted=True)
        d = self.build(run)
        row = self.unit(d, "smoke_test")
        self.assertIn("smoke_test_result.json missing, see <path>/artifacts/smoke.log", row["repairs"][0]["text"])
        self.assertEqual([line for line in row["log_tail"]["lines"] if out_dir in line], [])
        blob = json.dumps(d)
        self.assertNotIn(json.dumps(out_dir)[1:-1], blob)  # the blob escapes Windows backslashes
        self.assertNotIn("output_dir", blob)

    def test_output_dir_mount_alias_is_masked(self) -> None:
        real = Path(self.tmp.name) / "real-out"
        real.mkdir()
        link = Path(self.tmp.name) / "out"
        try:
            os.symlink(real, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:  # Windows without the create-symlink privilege
            self.skipTest(f"cannot create a symlink here: {exc}")
        canonical = os.path.realpath(link)
        self.assertNotEqual(canonical, str(link))  # otherwise the test proves nothing about aliases
        run = FakeRun(self.runs_root, "20260818-120000-eeee0012", "sure_eval", f"model=demo-model output_dir={link}", output_dir=str(link))
        run.pass_through("smoke_test")
        run.block("smoke_test", f"smoke_test_result.json missing, see {canonical}/x.log", exhausted=True)
        d = self.build(run)
        self.assertIn("smoke_test_result.json missing, see <path>/x.log", self.unit(d, "smoke_test")["repairs"][0]["text"])
        self.assertNotIn(json.dumps(canonical)[1:-1], json.dumps(d))

    def test_relative_output_dir_spelling_does_not_mask_words(self) -> None:
        # typed as a bare name, the spelling is a substring of ordinary words; only its resolved form is a path
        resolved = str(Path(self.tmp.name) / "out")
        run = FakeRun(self.runs_root, "20260818-120000-eeee0014", "sure_eval", "model=demo-model output_dir=out", output_dir=resolved)
        run.pass_through("smoke_test")
        run.block("smoke_test", f"smoke timeout, see {resolved}/x.log")
        text = self.unit(self.build(run), "smoke_test")["repairs"][0]["text"]
        self.assertEqual(text, "smoke timeout, see <path>/x.log")

    def test_prior_run_last_repair_masks_that_runs_output_dir(self) -> None:
        prior_out = str(Path(self.tmp.name) / "prior-out")
        prior = FakeRun(self.runs_root, "20260817-100000-prior000", "sure_eval", f"model=demo-model output_dir={prior_out}", output_dir=prior_out)
        prior.pass_through("smoke_test")
        prior.block("smoke_test", f"smoke_test_result.json missing, see {prior_out}/artifacts/smoke.log")
        prior.save()
        current = FakeRun(self.runs_root, "20260818-120000-eeee0013", "sure_eval", "model=demo-model output_dir=/tmp/current-out")
        last_repair = self.build(current)["prior_runs"][0]["last_repair"]
        self.assertNotIn(prior_out, last_repair)
        self.assertIn("see <path>/artifacts/smoke.log", last_repair)

    def test_prior_runs_same_skill_same_target_newest_first_limited(self) -> None:
        for i in range(7):  # same target, older than the current run
            prior = self.onboard_run(run_id=f"20260817-1000{i:02d}-prior{i:03d}")
            prior.pass_through("build_env")
            prior.block("build_env", f"prior {i} failure", shown_repair=f"prior {i} failure\n\n{MEMORY_BLOCK}")
            if i % 2 == 0:
                prior.finish("failed", error_summary=None)
            else:
                prior.shutdown("failed")
            prior.artifact("extraction_declaration.json", {"candidates": [f"01-torch-cu118-index-{i}", "02-second"]})
            prior.save()
        other_target = self.onboard_run(run_id="20260817-110000-other001", args="model_id=other/model")
        other_target.artifact("model_input_resolved.json", {"model_id": "other/model", "model_dir": "/x"})
        other_target.save()
        other_skill = FakeRun(self.runs_root, "20260817-120000-eval0001", "sure_eval", "model=openai/whisper-large-v3")
        other_skill.save()
        newer = self.onboard_run(run_id="20260819-000000-newer001")  # newer than the current run: still a sibling
        newer.finish("success")
        newer.save()
        current = self.onboard_run()
        d = self.build(current)
        prior_ids = [p["run_id"] for p in d["prior_runs"]]
        self.assertEqual(len(prior_ids), CONFIG["digest_limits"]["prior_runs"])
        self.assertEqual(prior_ids[0], "20260819-000000-newer001")
        self.assertEqual(prior_ids[1:], ["20260817-100006-prior006", "20260817-100005-prior005", "20260817-100004-prior004", "20260817-100003-prior003"])
        self.assertNotIn(current.run_id, prior_ids)
        newest = d["prior_runs"][0]
        self.assertEqual((newest["status"], newest["failed_unit"], newest["last_repair"], newest["candidates"]), ("success", None, None, []))
        p6 = d["prior_runs"][1]
        self.assertEqual((p6["status"], p6["failed_unit"], p6["finished_at"]), ("failed", "build_env", TS))
        self.assertEqual(p6["last_repair"], "prior 6 failure")  # from events: "finished" cleared lastRepair; Memory block stripped
        self.assertEqual(p6["candidates"], ["torch-cu118-index-6", "second"])
        p5 = d["prior_runs"][2]
        self.assertEqual(p5["last_repair"], "prior 5 failure")  # from run.json lastRepair, Memory block stripped

    def test_prior_run_last_repair_is_truncated(self) -> None:
        prior = self.onboard_run(run_id="20260817-100000-prior000")
        prior.pass_through("build_env")
        prior.block("build_env", "x" * 900)
        prior.save()
        d = self.build(self.onboard_run())
        self.assertEqual(len(d["prior_runs"][0]["last_repair"]), CONFIG["digest_limits"]["prior_run_repair_chars"])

    def test_prior_run_last_repair_source_says_who_wrote_the_text(self) -> None:
        gate_json = self.onboard_run(run_id="20260817-100003-gatejson")
        gate_json.pass_through("build_env")
        gate_json.block("build_env", "gate wrote this into run.json")
        gate_json.save()
        gate_events = self.onboard_run(run_id="20260817-100002-gateevts")
        gate_events.pass_through("build_env")
        gate_events.block("build_env", "gate wrote this into events.jsonl")
        gate_events.finish("failed", error_summary=None)  # the finish clears lastRepair
        gate_events.save()
        agent = self.onboard_run(run_id="20260817-100001-agentsum")
        agent.finish("failed", error_summary="the queue looked busy so I stopped here")
        agent.save()
        quiet = self.onboard_run(run_id="20260817-100000-quiet000")
        quiet.finish("success")
        quiet.save()
        rows = {p["run_id"]: (p["last_repair"], p["last_repair_source"]) for p in self.build(self.onboard_run())["prior_runs"]}
        self.assertEqual(rows["20260817-100003-gatejson"], ("gate wrote this into run.json", "gate"))
        self.assertEqual(rows["20260817-100002-gateevts"], ("gate wrote this into events.jsonl", "gate"))
        self.assertEqual(rows["20260817-100001-agentsum"], ("the queue looked busy so I stopped here", "agent"))
        self.assertEqual(rows["20260817-100000-quiet000"], (None, None))

    def test_prior_run_repair_beyond_the_first_seek_window_is_found(self) -> None:
        # every state_patch event carries the whole state, so a handful of agent turns after the
        # block push the repair well past the last log_seek_bytes of events.jsonl
        prior = self.onboard_run(run_id="20260817-100000-prior000")
        prior.pass_through("build_env")
        prior.block("build_env", "cu118 wheel is not on the index")
        prior.finish("failed", error_summary=None)
        prior.save()
        seek = CONFIG["digest_limits"]["log_seek_bytes"]
        events_path = prior.dir / "events.jsonl"
        padding = json.dumps({"type": "tool_call", "data": {"toolName": "bash", "input": {"command": "x" * 2000}}}) + "\n"
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(padding * (2 * seek // len(padding) + 2))
        self.assertGreater(events_path.stat().st_size, 2 * seek)
        row = self.build(self.onboard_run())["prior_runs"][0]
        self.assertEqual((row["last_repair"], row["last_repair_source"]), ("cu118 wheel is not on the index", "gate"))

    def test_prior_run_repair_search_stops_at_the_seek_cap(self) -> None:
        self.assertEqual(CONFIG["digest_limits"]["prior_run_seek_max_bytes"], 8 * 1024 * 1024)
        prior = self.onboard_run(run_id="20260817-100000-prior000")
        prior.pass_through("build_env")
        prior.block("build_env", "cu118 wheel is not on the index")
        prior.finish("failed", error_summary=None)
        prior.save()
        padding = json.dumps({"type": "tool_call", "data": {"toolName": "bash", "input": {"command": "x" * 2000}}}) + "\n"
        with (prior.dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(padding * 8)
        self.assertIsNone(digest._last_repair_from_events(prior.dir, HEADER, 512, 4096))
        self.assertEqual(digest._last_repair_from_events(prior.dir, HEADER, 512, 1 << 20), "cu118 wheel is not on the index")

    def test_a_prior_run_report_block_survives_a_success_finish(self) -> None:
        # run_report runs after extract_lessons, so its block never reaches that run's own digest;
        # extension.ts then clears lastRepair on the success finish and events.jsonl is all that is left
        repair = ('RUN_REPORT_UNIT completed-run execution gate failed:\n'
                  '  - successful run report conflicts with execution_result.json job_status "FAILED"')
        prior = FakeRun(self.runs_root, "20260817-100000-prior000", "sure_eval", "model=demo-model")
        prior.pass_through("run_report")
        prior.block("run_report", repair)
        prior.finish("success")
        prior.save()
        self.assertNotIn("lastRepair", json.loads((prior.dir / "run.json").read_text(encoding="utf-8")))
        current = FakeRun(self.runs_root, "20260818-120000-eeee0015", "sure_eval", "model=demo-model")
        row = self.build(current)["prior_runs"][0]
        self.assertIn("successful run report conflicts with execution_result.json job_status", row["last_repair"])
        self.assertEqual(row["last_repair_source"], "gate")

    def test_prior_runs_empty_when_target_unknown(self) -> None:
        prior = FakeRun(self.runs_root, "20260817-100000-prior000", "sure_eval", "")
        prior.save()
        current = FakeRun(self.runs_root, "20260818-120000-eeee0009", "sure_eval", "")
        self.assertEqual(self.build(current)["prior_runs"], [])

    def test_memory_usage_from_usage_jsonl(self) -> None:
        run = self.onboard_run()
        usage = self.memory_root / "usage" / f"{run.run_id}.jsonl"
        rows = [
            {"kind": "pre_start", "run_id": run.run_id, "skill": "sure_onboard", "entries": [{"entry_id": "_shared/vc-partition-names", "shared": True}], "at": TS},
            {"kind": "inject", "run_id": run.run_id, "skill": "sure_onboard", "unit": "build_env", "attempt": 1, "events_cutoff": 40,
             "entries": [{"entry_id": "sure_onboard/no-kernel-image", "shared": False}, {"entry_id": "sure_onboard/torch-cu118-index", "shared": False}], "at": TS},
            {"kind": "inject", "run_id": run.run_id, "skill": "sure_onboard", "unit": "validate_import", "attempt": 2, "events_cutoff": 90,
             "entries": [{"entry_id": "sure_onboard/no-kernel-image", "shared": False}], "at": TS},
            {"kind": "settle", "run_id": run.run_id, "skill": "sure_onboard", "unit": "build_env", "entry_id": "sure_onboard/no-kernel-image", "outcome": "useful_activated", "at": TS},
            {"kind": "settle", "run_id": run.run_id, "skill": "sure_onboard", "unit": "build_env", "entry_id": "sure_onboard/torch-cu118-index", "outcome": "disputed", "at": TS},
        ]
        for row in rows:
            paths.append_jsonl(usage, row, 4096)
        with usage.open("a", encoding="utf-8") as handle:
            handle.write("{broken\n")
        d = self.build(run)
        self.assertEqual(d["run"]["memory_usage"], [
            {"entry_id": "sure_onboard/no-kernel-image", "unit": "build_env", "attempt": 1, "outcome": "useful"},
            {"entry_id": "sure_onboard/torch-cu118-index", "unit": "build_env", "attempt": 1, "outcome": "disputed"},
            {"entry_id": "sure_onboard/no-kernel-image", "unit": "validate_import", "attempt": 2, "outcome": "open"},
        ])

    def test_memory_usage_empty_without_usage_file(self) -> None:
        self.assertEqual(self.build(self.onboard_run())["run"]["memory_usage"], [])

    def test_memory_index_snapshot_from_index_json(self) -> None:
        entry = {
            "entry_id": "sure_onboard/no-kernel-image", "type": "bad_case", "status": "confirmed", "target_skill": "sure_onboard", "applies_to": ["sure_onboard"],
            "component": "build_env", "cause": "cuda_version_mismatch", "trigger": ["no kernel image is available"], "scope": None, "title": "CUDA arch mismatch",
            "path": "sure/skills/sure_onboard/references/memory/bad_cases/no-kernel-image.md", "legacy": True, "op": "add", "target_entry": None, "similar_entry": None,
            "useful_activated": 3, "useful_unattributed": 1, "injections": 5, "disputed": 1, "created": "legacy", "checked_at": None, "stale": False, "superseded_by": None,
        }
        gone = dict(entry, entry_id="sure_onboard/old-one", status="superseded", superseded_by="sure_onboard/no-kernel-image")
        rejected = dict(entry, entry_id="sure_onboard/bad-one", status="rejected")
        paths.atomic_write_json(self.memory_root / "index.json", {"schema": "sure.memory.index.v1", "built_at": TS, "sources_sha256": "0" * 64, "entries": [entry, gone, rejected], "omitted_provisional": 0})
        d = self.build(self.onboard_run())
        self.assertEqual(d["memory_index_snapshot"], [{
            "id": "sure_onboard/no-kernel-image", "type": "bad_case", "status": "confirmed", "target_skill": "sure_onboard", "component": "build_env",
            "cause": "cuda_version_mismatch", "trigger": ["no kernel image is available"], "useful": 3, "disputed": 1,
        }])

    def test_memory_index_snapshot_empty_when_index_missing_or_unknown_schema(self) -> None:
        self.assertEqual(self.build(self.onboard_run())["memory_index_snapshot"], [])
        paths.atomic_write_json(self.memory_root / "index.json", {"schema": "sure.memory.index.v9", "entries": [{"entry_id": "x"}]})
        self.assertEqual(self.build(self.onboard_run(run_id="20260818-120000-aaaa0003"))["memory_index_snapshot"], [])

    def test_units_registry_lists_all_skills(self) -> None:
        d = self.build(self.onboard_run())
        self.assertEqual(d["units_registry"], UNITS["skills"])
        self.assertIn("extract_lessons", d["units_registry"]["sure_onboard"])

    def test_error_digest_when_run_dir_unreadable(self) -> None:
        not_a_dir = self.runs_root / "20260818-120000-file0001"
        not_a_dir.write_text("not a directory", encoding="utf-8")
        d = digest.build_run_digest(not_a_dir, self.repo_root, cutoff=None, mark_passed=None, config=CONFIG, units=UNITS, log_paths=LOG_PATHS)
        self.assertEqual(set(d), {"schema", "error"})
        self.assertEqual(d["schema"], "sure.memory.run_digest.v1")
        self.assertIn("20260818-120000-file0001", d["error"])
        self.assertNotIn(str(self.runs_root), d["error"])


class TrimTests(unittest.TestCase):
    @staticmethod
    def fat_digest() -> dict:
        limits = CONFIG["digest_limits"]
        repair = "R" * limits["repair_head_chars"] + "\n...[900 chars omitted]...\n" + "T" * limits["repair_tail_chars"]
        unit = {
            "id": "build_env", "outcome": "failed", "attempts": 3,
            "repairs": [{"attempt": i + 1, "text": repair} for i in range(3)],
            "fix_window": [{"tool": "bash", "command": f"cmd {i} " + "c" * 290} for i in range(10)],
            "last_commands": [{"tool": "bash", "command": f"last {i} " + "c" * 290} for i in range(10)],
            "log_tail": {"path": "{run_dir}/artifacts/build_env.log", "lines": [f"line {i} " + "l" * 290 for i in range(30)]},
        }
        snapshot = [{"id": f"sure_onboard/entry-{i}", "type": "bad_case", "status": "provisional", "target_skill": "sure_onboard", "component": "build_env",
                     "cause": "infra", "trigger": [f"trigger phrase number {i} " + "t" * 80], "useful": 0, "disputed": 0} for i in range(120)]
        prior = [{"run_id": f"20260817-10000{i}-prior00{i}", "status": "failed", "failed_unit": "build_env", "finished_at": TS, "last_repair": "p" * 300, "candidates": ["a", "b"]} for i in range(5)]
        return {
            "schema": "sure.memory.run_digest.v1",
            "run": {"run_id": "20260818-120000-aaaa0001", "skill": "sure_onboard", "args": "model_id=x", "target": {"kind": "model", "id": "x"}, "status_so_far": "running", "cutoff": 10, "memory_usage": []},
            "units": [dict(unit, id=uid) for uid in ("build_env", "validate_import", "validate_load")],
            "tool_errors": 0,
            "prior_runs": prior,
            "memory_index_snapshot": snapshot,
            "units_registry": UNITS["skills"],
        }

    def test_noop_when_under_budget(self) -> None:
        small = {"schema": "sure.memory.run_digest.v1", "run": {"skill": "sure_onboard"}, "units": [], "prior_runs": [], "memory_index_snapshot": [], "units_registry": UNITS["skills"]}
        self.assertEqual(digest.trim_to_budget(small, CONFIG), small)

    def test_steps_apply_in_config_order_and_stop_when_it_fits(self) -> None:
        fat = self.fat_digest()
        self.assertGreater(digest._digest_bytes(fat), CONFIG["digest_max_bytes"])
        cfg = dict(CONFIG, digest_max_bytes=digest._digest_bytes(fat) - 5000)  # the snapshot step alone saves more than that
        trimmed = digest.trim_to_budget(fat, cfg)
        self.assertEqual(set(trimmed["memory_index_snapshot"][0]), {"id", "status", "target_skill", "component", "cause"})
        self.assertEqual(trimmed["units_registry"], UNITS["skills"])  # second step never ran
        self.assertEqual(len(trimmed["prior_runs"]), 5)
        self.assertEqual(len(fat["memory_index_snapshot"][0]), 9)  # caller's dict untouched

    def test_full_trim_keeps_repairs_and_fix_window(self) -> None:
        fat = self.fat_digest()
        cfg = dict(CONFIG, digest_max_bytes=1000)  # impossible budget: every step runs, overflow accepted
        trimmed = digest.trim_to_budget(fat, cfg)
        self.assertEqual(trimmed["units_registry"], {"sure_onboard": UNITS["skills"]["sure_onboard"]})
        self.assertEqual(len(trimmed["prior_runs"]), 2)
        self.assertNotIn("candidates", trimmed["prior_runs"][0])
        for unit in trimmed["units"]:
            self.assertEqual(len(unit["log_tail"]["lines"]), 10)
            self.assertEqual(len(unit["repairs"]), 3)
            for rep in unit["repairs"]:
                self.assertTrue(rep["text"].startswith("R" * 100))
                self.assertTrue(rep["text"].endswith("T" * 200))
                self.assertLess(len(rep["text"]), 300 + 40)
            self.assertEqual(len(unit["fix_window"]), 5)
            self.assertEqual(unit["fix_window"][0]["command"][:6], "cmd 5 ")
            self.assertEqual(len(unit["last_commands"]), 10)  # last_commands is not a trim target
        self.assertGreater(digest._digest_bytes(trimmed), 1000)

    def test_repairs_300_marker_counts_what_the_first_clip_dropped_too(self) -> None:
        # _unit_row already clipped this text, so re-clipping it counts only what the second clip
        # dropped: the digest told the agent 327 characters were missing out of 1132.
        limits = CONFIG["digest_limits"]
        raw = "H" * 700 + "M" * 332 + "T" * 400
        once = digest.clip_head_tail(raw, limits["repair_head_chars"], limits["repair_tail_chars"])
        d = {"schema": "sure.memory.run_digest.v1", "run": {"skill": "sure_onboard"},
             "units": [{"id": "build_env", "repairs": [{"attempt": 1, "text": once}], "fix_window": []}]}
        digest._trim_step(d, "repairs_300", CONFIG)
        text = d["units"][0]["repairs"][0]["text"]
        head, tail = limits["repair_head_chars"] // 2, limits["repair_tail_chars"] // 2
        self.assertTrue(text.startswith("H" * head))
        self.assertTrue(text.endswith("T" * tail))
        omitted = int(re.search(r"\[(\d+) chars omitted\]", text).group(1))
        self.assertEqual(omitted + head + tail, len(raw))  # shown + omitted == the original repair

    def test_repairs_300_on_an_unclipped_repair_is_a_plain_clip(self) -> None:
        raw = "H" * 700 + "T" * 400
        d = {"schema": "sure.memory.run_digest.v1", "run": {"skill": "sure_onboard"},
             "units": [{"id": "build_env", "repairs": [{"attempt": 1, "text": raw}], "fix_window": []}]}
        digest._trim_step(d, "repairs_300", CONFIG)
        limits = CONFIG["digest_limits"]
        head, tail = limits["repair_head_chars"] // 2, limits["repair_tail_chars"] // 2
        self.assertEqual(d["units"][0]["repairs"][0]["text"], digest.clip_head_tail(raw, head, tail))

    def test_build_run_digest_trims_real_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            runs_root = repo_root / ".sure" / "runs"
            runs_root.mkdir(parents=True)
            run = FakeRun(runs_root, "20260818-120000-aaaa0001", "sure_onboard", "model_id=x")
            run.pass_through("build_env")
            for i in range(60):
                run.block("build_env", f"attempt {i}: " + "z" * 1500)
                run.bash(f"pip install fix-{i} " + "q" * 400)
            run.pass_unit("build_env")
            run.save()
            d = digest.build_run_digest(run.dir, repo_root, cutoff=None, mark_passed=None, config=CONFIG, units=UNITS, log_paths=LOG_PATHS)
            self.assertNotIn("error", d)
            self.assertEqual(d["units_registry"], {"sure_onboard": UNITS["skills"]["sure_onboard"]})  # trimmed
            build_env = [u for u in d["units"] if u["id"] == "build_env"][0]
            self.assertEqual(len(build_env["repairs"]), 60)
            self.assertLess(len(build_env["repairs"][0]["text"]), 340)  # repairs_300 ran
            self.assertEqual(len(build_env["fix_window"]), 5)  # fix_window_5 ran, window kept
            self.assertGreater(digest._digest_bytes(d), CONFIG["digest_max_bytes"])  # overflow accepted


class IndexSnapshotCapTests(DigestTestBase):
    """memory_index_snapshot is the one part of the digest sized by the library rather than by the
    run, so it is the one part that grows past digest_max_bytes on a run that did nothing unusual.
    index_snapshot_min shrinks every row to five fields and stops there; nothing dropped a row."""

    SKILLS = ("sure_onboard", "sure_eval", "sure_feed")

    def write_index(self, count: int) -> list[dict]:
        """A realistic index.json: three target skills, the four live statuses, two triggers a row,
        rows in the order index.write_index lays them down (confirmed, provisional newest first,
        disputed). Returns the entries as written."""
        entries = []
        for i in range(count):
            skill = self.SKILLS[i % 3]
            entries.append({
                "entry_id": f"{skill}/entry-{i:03d}", "type": "bad_case",
                "status": ("confirmed", "provisional", "provisional", "disputed")[i % 4],
                "target_skill": skill, "applies_to": [skill], "component": "build_env",
                "cause": "cuda_version_mismatch", "scope": None, "title": f"entry {i} title",
                "trigger": [f"no kernel image is available for device {i}",
                            f"torch was compiled for sm_80 (entry {i})"],
                "path": f"sure/memory/provisional/{skill}/entry-{i:03d}/entry.md",
                "legacy": False, "op": "add", "target_entry": None, "similar_entry": None,
                "useful_activated": i % 3, "useful_unattributed": 0, "injections": i, "disputed": i % 2,
                "created": {"date": f"2026-0{1 + i % 8}-01"}, "checked_at": None, "stale": False,
                "superseded_by": None,
            })
        ordered = index_lib.ordered_entries(entries)
        paths.atomic_write_json(self.memory_root / "index.json", {
            "schema": "sure.memory.index.v1", "built_at": TS, "sources_sha256": "0" * 64,
            "entries": ordered, "omitted_provisional": 0,
        })
        return ordered

    def blocked_run(self) -> FakeRun:
        run = self.onboard_run()
        run.pass_through("build_env")
        run.block("build_env", "attempt 1: " + "z" * 1200)
        run.bash("pip install torch " + "q" * 300)
        return run

    def test_large_index_still_fits_the_digest_budget(self) -> None:
        # 150 entries built a 30kB digest against a 20480 byte budget, exit 0 and no error field.
        self.write_index(150)
        d = self.build(self.blocked_run())
        self.assertNotIn("error", d)
        self.assertLessEqual(digest._digest_bytes(d), CONFIG["digest_max_bytes"])

    def test_dropped_rows_are_counted_in_the_snapshot_itself(self) -> None:
        # A short list with no marker reads exactly like a small library: the same silent lie the
        # overrun was, one level down. The count lives in the list the agent is already reading.
        self.write_index(150)
        snapshot = self.build(self.blocked_run())["memory_index_snapshot"]
        marker = snapshot[-1]
        self.assertEqual(set(marker), {"omitted", "note"})
        self.assertGreater(marker["omitted"], 0)
        self.assertEqual(len(snapshot) - 1 + marker["omitted"], 150)
        self.assertIn("cli.py list", marker["note"])
        self.assertIn(str(marker["omitted"]), marker["note"])

    def test_the_cap_runs_before_the_run_s_own_evidence_is_trimmed(self) -> None:
        # Step 1 of the ladder already spends the snapshot first; finishing the snapshot before
        # moving on keeps that order. A cap at the end of the ladder would have paid for the same
        # rows with the registry, the log tails and the repair text of this run.
        self.write_index(150)
        d = self.build(self.blocked_run())
        self.assertEqual(d["units_registry"], UNITS["skills"])  # registry_current_only never ran
        repair = self.unit(d, "build_env")["repairs"][0]["text"]
        self.assertGreater(len(repair), 500)  # repairs_300 never ran either

    def test_drop_order_is_provisional_oldest_first_then_disputed_then_confirmed(self) -> None:
        # index.render_index_md drops the oldest provisional lines and keeps confirmed to the end;
        # these are the same rows, so they go in the same order. index.json holds provisional
        # newest first, so the tail of each status run is its oldest member.
        rows = [{"id": "a", "status": "confirmed"}, {"id": "b", "status": "confirmed"},
                {"id": "c", "status": "provisional"}, {"id": "d", "status": "provisional"},
                {"id": "e", "status": "disputed"}, {"id": "f", "status": "somethingelse"}]
        self.assertEqual([rows[i]["id"] for i in digest._snapshot_drop_order(rows)],
                         ["f", "d", "c", "e", "b", "a"])

    def test_confirmed_rows_are_the_last_to_go(self) -> None:
        ordered = self.write_index(150)
        kept = {r["id"] for r in self.build(self.blocked_run())["memory_index_snapshot"] if "id" in r}
        confirmed = {e["entry_id"] for e in ordered if e["status"] == "confirmed"}
        self.assertLess(len(kept), 150)  # rows did go
        self.assertEqual(confirmed - kept, set())  # none of them confirmed

    def test_index_snapshot_min_leaves_the_marker_alone(self) -> None:
        # The two snapshot steps are ordered data, and a trim order that runs the cap first would
        # otherwise hand index_snapshot_min the marker and get back a row of five nulls: an entry
        # the index never had, in place of the count of the ones it lost.
        d = {"schema": "sure.memory.run_digest.v1", "run": {"skill": "sure_onboard"},
             "memory_index_snapshot": [{"id": "sure_onboard/a", "status": "confirmed", "type": "bad_case",
                                        "target_skill": "sure_onboard", "component": "build_env",
                                        "cause": "infra", "trigger": ["t"], "useful": 0, "disputed": 0},
                                       digest._omitted_row(41)]}
        digest._trim_step(d, "index_snapshot_min", CONFIG)
        self.assertEqual(d["memory_index_snapshot"][-1], digest._omitted_row(41))

    def test_the_step_joins_a_trim_order_written_before_it_existed(self) -> None:
        # config.json is hand-tuned and no upgrade rewrites it, so every deployed file lists the
        # ladder without this step; skipping it there would leave the overrun exactly as it was.
        self.assertNotIn(digest.SNAPSHOT_FIT_STEP, CONFIG["digest_trim_order"])
        self.assertEqual(
            digest._trim_order({"digest_trim_order": ["index_snapshot_min", "registry_current_only"]}),
            ["index_snapshot_min", digest.SNAPSHOT_FIT_STEP, "registry_current_only"])
        listed = ["index_snapshot_min", digest.SNAPSHOT_FIT_STEP, "repairs_300"]
        self.assertEqual(digest._trim_order({"digest_trim_order": listed}), listed)


class MainTests(DigestTestBase):
    def run_main(self, argv: list[str]) -> tuple[int, str]:
        buffer, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(errors):
            code = digest.main(argv)
        return code, buffer.getvalue()

    def test_writes_default_path_and_prints_sha(self) -> None:
        run = self.onboard_run()
        run.pass_through("plan")
        run.save()
        code, out = self.run_main(["--run-dir", str(run.dir), "--repo-root", str(self.repo_root)])
        self.assertEqual(code, 0)
        target = run.dir / "artifacts" / "run_digest.json"
        self.assertTrue(target.exists())
        report = json.loads(out)
        self.assertEqual(report["sha256"], paths.sha256_file(target))
        self.assertIsNone(report["error"])
        written = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(written["run"]["run_id"], run.run_id)
        self.assertEqual(written["units"][-1]["id"], "plan")

    def test_cutoff_mark_passed_skill_and_finish_status_flags(self) -> None:
        run = self.onboard_run()
        run.pass_through("verdict")
        cutoff = run.event_count()
        run.bash("later")
        run.save()
        code, _ = self.run_main(["--run-dir", str(run.dir), "--repo-root", str(self.repo_root), "--cutoff", str(cutoff), "--mark-passed", "verdict", "--finish-status", "incomplete", "--skill", "sure_onboard"])
        self.assertEqual(code, 0)
        written = json.loads((run.dir / "artifacts" / "run_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(written["run"]["cutoff"], cutoff)
        self.assertEqual(written["run"]["status_so_far"], "incomplete")
        self.assertEqual([u["id"] for u in written["units"]][-2:], ["verdict", "extract_lessons"])
        self.assertEqual(written["units"][-2]["outcome"], "passed")

    def test_out_relative_to_run_dir_leaves_hook_digest_alone(self) -> None:
        run = self.onboard_run()
        run.save()
        code, out = self.run_main(["--run-dir", str(run.dir), "--repo-root", str(self.repo_root), "--out", "artifacts/run_digest.preview.json"])
        self.assertEqual(code, 0)
        self.assertTrue((run.dir / "artifacts" / "run_digest.preview.json").exists())
        self.assertFalse((run.dir / "artifacts" / "run_digest.json").exists())
        self.assertEqual(json.loads(out)["path"], str(run.dir / "artifacts" / "run_digest.preview.json"))

    def test_error_digest_is_written_and_exit_code_is_one(self) -> None:
        not_a_dir = self.runs_root / "20260818-120000-file0002"
        not_a_dir.write_text("x", encoding="utf-8")
        out_path = Path(self.tmp.name) / "preview.json"
        code, out = self.run_main(["--run-dir", str(not_a_dir), "--repo-root", str(self.repo_root), "--out", str(out_path)])
        self.assertEqual(code, 1)
        written = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(set(written), {"schema", "error"})
        self.assertEqual(json.loads(out)["error"], written["error"])

    def run_main_verbose(self, argv: list[str]) -> tuple[int, str, str]:
        buffer, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(errors):
            code = digest.main(argv)
        return code, buffer.getvalue(), errors.getvalue()

    def test_config_without_the_budget_key_is_an_error_digest_not_a_traceback(self) -> None:
        # trim_to_budget used to run outside build_run_digest's own except, so a malformed config
        # threw a traceback -- library paths and all -- which buildDigest copies into the digest's
        # error field and a diagnostic. The contract is exit 0/1/2 with a JSON line.
        run = self.onboard_run()
        run.save()
        broken = {key: value for key, value in CONFIG.items() if key != "digest_max_bytes"}
        with patch.object(digest.paths, "load_config", return_value=broken):
            code, _out, err = self.run_main_verbose(["--run-dir", str(run.dir), "--repo-root", str(self.repo_root)])
        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        written = json.loads((run.dir / "artifacts" / "run_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(written), {"schema", "error"})
        self.assertIn("digest_max_bytes", written["error"])
        for absolute in (run.dir, self.repo_root, Path(digest.__file__).parent):
            self.assertNotIn(json.dumps(str(absolute))[1:-1], json.dumps(written))

    def test_digest_that_cannot_be_read_back_still_prints_one_json_line(self) -> None:
        run = self.onboard_run()
        run.save()
        with patch.object(digest.paths, "sha256_file", side_effect=OSError(13, "permission denied")):
            code, out, err = self.run_main_verbose(["--run-dir", str(run.dir), "--repo-root", str(self.repo_root)])
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err)
        report = json.loads(out)
        self.assertIsNone(report["sha256"])
        self.assertIsNone(report["bytes"])
        self.assertTrue((run.dir / "artifacts" / "run_digest.json").exists())

    def test_unwritable_output_returns_two(self) -> None:
        not_a_dir = self.runs_root / "20260818-120000-file0003"
        not_a_dir.write_text("x", encoding="utf-8")
        code, _ = self.run_main(["--run-dir", str(not_a_dir), "--repo-root", str(self.repo_root)])
        self.assertEqual(code, 2)


class WrapperTests(DigestTestBase):
    def test_skill_wrappers_call_the_shared_module(self) -> None:
        run = self.onboard_run()
        run.save()
        for skill in ("sure_onboard", "sure_eval", "sure_trans", "sure_feed"):
            wrapper = REPO_ROOT / "sure" / "skills" / skill / "scripts" / "build_run_digest.py"
            with self.subTest(skill=skill):
                self.assertTrue(wrapper.exists(), wrapper)
                out = Path(self.tmp.name) / f"{skill}.json"
                proc = subprocess.run([sys.executable, "-s", str(wrapper), "--run-dir", str(run.dir), "--repo-root", str(self.repo_root), "--out", str(out)],
                                      capture_output=True, text=True, check=False, cwd=str(wrapper.parent))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["run"]["run_id"], run.run_id)


if __name__ == "__main__":
    unittest.main()
