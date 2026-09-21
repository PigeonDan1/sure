#!/usr/bin/env python3
"""Tests for agent_runner.py: chain execution with stub stages (no real models/APIs).

Run directly (needs the Harness Python for yaml/pydantic):
    cd sure/skills/sure_agent_eval/scripts && python3 -m unittest test_agent_runner.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_runner  # noqa: E402
from sure_eval.datasets import source_resolver  # noqa: E402

STUB_MCP_SERVER = """\
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "stub", "version": "0"}, "capabilities": {"tools": {}}}
    elif method == "tools/call":
        arguments = (request.get("params") or {}).get("arguments") or {}
        audio_path = str(arguments.get("audio_path", ""))
        if "boom" in audio_path:
            result = {"isError": True, "content": [{"type": "text", "text": "stub tool failure"}]}
        else:
            result = {"content": [{"type": "text", "text": json.dumps({"text": "TRANSCRIBED:" + audio_path})}]}
    elif method == "shutdown":
        result = {}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}) + "\\n")
    sys.stdout.flush()
"""


HANGING_MCP_SERVER = """\
import sys, time
sys.stdin.readline()
time.sleep(30)
"""


ENV_ECHO_MCP_SERVER = """\
import json, os, sys
sys.stderr.write("stub server log line\\n")
sys.stderr.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    if request.get("method") == "tools/call":
        result = {"content": [{"type": "text", "text": json.dumps({"text": os.environ.get("MODEL_PATH", "")})}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}) + "\\n")
    sys.stdout.flush()
"""


def write_flat_s2tt_source(root: Path, name: str, samples: int = 2) -> Path:
    dataset_root = root / name
    dataset_root.mkdir(parents=True)
    lines = []
    for index in range(samples):
        audio = dataset_root / f"utt{index}.wav"
        audio.write_bytes(b"RIFFxxxx")
        lines.append(
            json.dumps(
                {
                    "sample_id": f"utt{index}",
                    "attribute": {"path": f"utt{index}.wav", "sample_rate": 16000},
                    "annotation": [
                        {"transcription": {"text": [f"源文本{index}"]}},
                        {"translation": {"text": [f"reference {index}"]}},
                    ],
                },
                ensure_ascii=False,
            )
        )
    (dataset_root / "sample.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (dataset_root / "ds.jsonl").write_text(
        '{"audio": {"speech": {"language": "zh", "translation_language": "en"}}}\n', encoding="utf-8"
    )
    return dataset_root


def write_versioned_s2tt_source(root: Path, name: str, versions: tuple[str, ...], samples: int = 2) -> Path:
    """A dataset pool in the versioned layout: sample_files/<version>/sample.jsonl."""
    pool = root / name
    raw_dir = pool / "raws" / "sample"
    raw_dir.mkdir(parents=True)
    for version in versions:
        version_dir = pool / "sample_files" / version
        version_dir.mkdir(parents=True)
        lines = []
        for index in range(samples):
            (raw_dir / f"{version}_utt{index}.wav").write_bytes(b"RIFFxxxx")
            lines.append(
                json.dumps(
                    {
                        "sample_id": f"{version}_utt{index}",
                        "attribute": {"path": f"{version}_utt{index}.wav", "sample_rate": 16000},
                        "annotation": [
                            {"transcription": {"text": [f"源文本{index}"]}},
                            {"translation": {"text": [f"reference {index}"]}},
                        ],
                    },
                    ensure_ascii=False,
                )
            )
        (version_dir / "sample.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (version_dir / "ds.jsonl").write_text(
            '{"audio": {"speech": {"language": "zh", "translation_language": "en"}}}\n', encoding="utf-8"
        )
    return pool


def make_spec(dataset_root: Path, product_dir: Path) -> dict:
    return {
        "schema": "sure.agent_eval.spec_resolved.v1",
        "run_id": "run_test",
        "created_at": "2026-09-10T00:00:00Z",
        "agent": {
            "name": "demo_s2tt",
            "task": "s2tt",
            "input": "speech",
            "output": "text",
            "spec_path": "/tmp/agent.yaml",
            "spec_sha256": "0" * 64,
        },
        "stages": [
            {
                "id": "asr",
                "model": "asr_model",
                "mode": "mcp_tool",
                "task": "ASR",
                "model_dir": "/tmp/models/asr_model",
                "config_path": "/tmp/models/asr_model/config.yaml",
                "verdict_path": "/tmp/models/asr_model/verdict.json",
                "tool_names": ["asr_transcribe"],
                "server_command": ["python", "server.py"],
                "working_dir": "/tmp/models/asr_model",
                "env": {},
                "api": None,
                "prompt_template": None,
                "deployment_bound": False,
                "deployment_error": "not sealed",
            },
            {
                "id": "translate",
                "model": "llm_model",
                "mode": "api",
                "task": "LLM",
                "model_dir": "/tmp/models/llm_model",
                "config_path": "/tmp/models/llm_model/config.yaml",
                "verdict_path": "/tmp/models/llm_model/verdict.json",
                "tool_names": [],
                "server_command": [],
                "working_dir": "/tmp/models/llm_model",
                "env": {},
                "api": {
                    "base_url": "https://example.invalid/v1",
                    "api_key_env": "DEMO_API_KEY",
                    "model": "qwen-mt",
                    "timeout": 60,
                    "retry": 2,
                },
                "prompt_template": "Translate to {target_language}: {text}",
                "deployment_bound": False,
                "deployment_error": "not sealed",
            },
        ],
        "datasets": [
            {
                "dataset": "mini_s2tt__unversioned",
                "source_root": str(dataset_root),
                "source_dataset_name": "mini_s2tt",
                "version_id": "unversioned",
                "task": "S2TT",
                "language": "zh",
                "translation_language": "en",
                "sample_jsonl": str(dataset_root / "sample.jsonl"),
                "ds_jsonl": str(dataset_root / "ds.jsonl"),
                "raw_dir": str(dataset_root),
                "num_samples": 2,
            }
        ],
        "metrics": ["bleu", "chrf"],
        "runtime": {"product_dir": str(product_dir), "output_dir": None, "dataset_source_key": "default"},
    }


class RunAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source_root = self.tmp / "src"
        self.dataset_root = write_flat_s2tt_source(self.source_root, "mini_s2tt")
        self.run_dir = self.tmp / "run"
        (self.run_dir / "artifacts").mkdir(parents=True)
        self.product_dir = self.tmp / "product"
        self.spec = make_spec(self.dataset_root, self.product_dir)
        self._env = mock.patch.dict(os.environ, {source_resolver.SOURCE_ROOT_ENV: str(self.source_root)})
        self._env.start()
        self._projection = mock.patch.object(agent_runner, "_projection_root", return_value=self.tmp / "projection")
        self._projection.start()
        self.clients: list[agent_runner.McpToolClient] = []

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()
        self._projection.stop()
        self._env.stop()
        self._tmp.cleanup()

    def stub_mcp_factory(self, stage: dict):
        def call(arguments: dict):
            return {"text": "TRANSCRIBED:" + Path(str(arguments["audio_path"])).stem}

        return call

    def stub_api_caller(self, api: dict, prompt: str) -> str:
        return "TRANSLATED:" + prompt.split(": ", 1)[-1]

    def test_chain_produces_a_compatible_bundle(self) -> None:
        result = agent_runner.run_agent(
            self.spec,
            self.run_dir,
            mcp_caller_factory=self.stub_mcp_factory,
            api_caller=self.stub_api_caller,
        )
        self.assertEqual(result["job_status"], "succeeded")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["product_dir"], str(self.product_dir))
        self.assertEqual(result["datasets"], [{"dataset": "mini_s2tt__unversioned", "expected": 2, "generated": 2}])

        predictions = (self.product_dir / "predictions" / "mini_s2tt__unversioned.txt").read_text(encoding="utf-8")
        rows = [line.split("\t") for line in predictions.splitlines()]
        self.assertEqual(rows, [["utt0", "TRANSLATED:TRANSCRIBED:utt0"], ["utt1", "TRANSLATED:TRANSCRIBED:utt1"]])

        manifest = json.loads((self.product_dir / "predictions" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["datasets"]["mini_s2tt__unversioned"]["rows"], 2)
        status = json.loads((self.product_dir / "prediction_generation_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["schema"], "sure.eval.prediction_generation_status.v2")
        self.assertEqual(status["datasets"][0]["status"], "completed")
        self.assertTrue((self.product_dir / "protocol.yaml").is_file())
        reference = self.product_dir / "references" / "sure_benchmark" / "jsonl" / "mini_s2tt__unversioned.jsonl"
        ref_rows = [json.loads(line) for line in reference.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(ref_rows[0]["task"], "S2TT")
        self.assertEqual(ref_rows[0]["target"], "reference 0")
        self.assertEqual(ref_rows[0]["source"], "源文本0")

        execution = json.loads((self.run_dir / "artifacts" / "execution_result.json").read_text(encoding="utf-8"))
        self.assertEqual(execution["job_status"], "succeeded")
        self.assertEqual(execution["schema"], "sure.agent_eval.execution_result.v1")

    def test_max_samples_bounds_the_run(self) -> None:
        result = agent_runner.run_agent(
            self.spec,
            self.run_dir,
            max_samples=1,
            mcp_caller_factory=self.stub_mcp_factory,
            api_caller=self.stub_api_caller,
        )
        self.assertEqual(result["job_status"], "succeeded")
        self.assertEqual(result["datasets"][0]["generated"], 1)

    def test_stage_failure_is_a_terminal_failed_record(self) -> None:
        def failing_api(api: dict, prompt: str) -> str:
            raise RuntimeError("boom")

        result = agent_runner.run_agent(
            self.spec,
            self.run_dir,
            mcp_caller_factory=self.stub_mcp_factory,
            api_caller=failing_api,
        )
        self.assertEqual(result["job_status"], "failed")
        self.assertEqual(result["failed_stage"], "translate")
        self.assertEqual(result["failed_dataset"], "mini_s2tt__unversioned")
        self.assertIn("boom", result["error"])
        execution = json.loads((self.run_dir / "artifacts" / "execution_result.json").read_text(encoding="utf-8"))
        self.assertEqual(execution["job_status"], "failed")

    def test_versioned_pool_projects_the_planned_version(self) -> None:
        pool = write_versioned_s2tt_source(self.source_root, "pool_s2tt", ("v1", "v2"))
        dataset = dict(self.spec["datasets"][0])
        dataset.update(
            {
                "dataset": "pool_s2tt__v2",
                "source_root": str(pool),
                "source_dataset_name": "pool_s2tt",
                "version_id": "v2",
                "sample_jsonl": str(pool / "sample_files" / "v2" / "sample.jsonl"),
                "ds_jsonl": str(pool / "sample_files" / "v2" / "ds.jsonl"),
                "raw_dir": str(pool / "raws" / "sample"),
            }
        )
        result = agent_runner.run_agent(
            {**self.spec, "datasets": [dataset]},
            self.run_dir,
            mcp_caller_factory=self.stub_mcp_factory,
            api_caller=self.stub_api_caller,
        )
        self.assertEqual(result["job_status"], "succeeded", result["error"])
        predictions = (self.product_dir / "predictions" / "pool_s2tt__v2.txt").read_text(encoding="utf-8")
        self.assertEqual([line.split("\t")[0] for line in predictions.splitlines()], ["v2_utt0", "v2_utt1"])

    def test_dataset_projection_failure_is_a_terminal_failed_record(self) -> None:
        class ExplodingManager:
            def download_and_convert(self, entry: str):
                raise RuntimeError("projection exploded")

        with mock.patch.object(agent_runner, "_dataset_manager", return_value=ExplodingManager()):
            result = agent_runner.run_agent(
                self.spec,
                self.run_dir,
                mcp_caller_factory=self.stub_mcp_factory,
                api_caller=self.stub_api_caller,
            )
        self.assertEqual(result["job_status"], "failed")
        self.assertEqual(result["failed_stage"], "dataset_projection")
        self.assertEqual(result["failed_dataset"], "mini_s2tt__unversioned")
        self.assertIn("projection exploded", result["error"])

    def start_stub_client(
        self,
        source: str = STUB_MCP_SERVER,
        *,
        stage: dict | None = None,
        log_path: Path | None = None,
    ) -> agent_runner.McpToolClient:
        server = self.tmp / "stub_server.py"
        server.write_text(source, encoding="utf-8")
        stage = dict(stage or self.spec["stages"][0])
        stage["server_command"] = [sys.executable, str(server)]
        stage["working_dir"] = str(self.tmp)
        client = agent_runner.McpToolClient(stage, log_path=log_path)
        self.clients.append(client)
        return client

    def test_real_mcp_client_against_a_stub_server(self) -> None:
        answer = self.start_stub_client().call({"audio_path": "/audio/utt0.wav"})
        self.assertEqual(agent_runner.extract_text(answer), "TRANSCRIBED:/audio/utt0.wav")

    def test_failed_handshake_kills_the_server(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = io.StringIO()
                self.stdout = io.StringIO("")  # the server dies during initialize
                self.killed = False

            def kill(self) -> None:
                self.killed = True

            def wait(self, timeout: float | None = None) -> int:
                return 1

        process = FakeProcess()
        with mock.patch.object(agent_runner.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "exited while answering initialize"):
                agent_runner.McpToolClient(dict(self.spec["stages"][0]))
        self.assertTrue(process.killed)

    def test_a_server_that_never_answers_times_out(self) -> None:
        with mock.patch.object(agent_runner, "MCP_RESPONSE_TIMEOUT_SEC", 0.5):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.start_stub_client(HANGING_MCP_SERVER)

    def test_tool_result_flagged_is_error_is_rejected(self) -> None:
        client = self.start_stub_client()
        with self.assertRaisesRegex(RuntimeError, "stub tool failure"):
            client.call({"audio_path": "/audio/boom.wav"})

    def test_stage_env_reaches_the_server_and_stderr_is_logged(self) -> None:
        stage = {**self.spec["stages"][0], "env": {"MODEL_PATH": "/models/asr_model/weights"}}
        log_path = self.tmp / "agent_runner.log"
        client = self.start_stub_client(ENV_ECHO_MCP_SERVER, stage=stage, log_path=log_path)
        answer = client.call({"audio_path": "/audio/utt0.wav"})
        self.assertEqual(agent_runner.extract_text(answer), "/models/asr_model/weights")
        client.close()
        self.assertIn("stub server log line", log_path.read_text(encoding="utf-8"))

    def test_api_caller_requires_the_credential_env_var(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                agent_runner.call_chat_completion(self.spec["stages"][1]["api"], "prompt")
        self.assertIn("DEMO_API_KEY", str(ctx.exception))

    def test_api_caller_sends_explicit_generation_parameters(self) -> None:
        captured: dict[str, object] = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return Response()

        api = {
            **self.spec["stages"][1]["api"],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 256,
        }
        with mock.patch.dict(os.environ, {"DEMO_API_KEY": "local-test-key"}), mock.patch.object(
            agent_runner.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            result = agent_runner.call_chat_completion(api, "Translate this")
        self.assertEqual(result, "ok")
        self.assertEqual(captured["body"]["temperature"], 0.0)
        self.assertEqual(captured["body"]["top_p"], 1.0)
        self.assertEqual(captured["body"]["max_tokens"], 256)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer local-test-key")

    def test_api_caller_rejects_empty_completion(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":""}}]}'

        api = self.spec["stages"][1]["api"]
        with mock.patch.dict(os.environ, {"DEMO_API_KEY": "local-test-key"}), mock.patch.object(
            agent_runner.urllib.request, "urlopen", return_value=Response()
        ), mock.patch.object(agent_runner.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "empty completion"):
                agent_runner.call_chat_completion({**api, "retry": 1}, "prompt")


class MainArgumentTests(unittest.TestCase):
    def run_main(self, *extra: str) -> int:
        """main() over a resolved spec whose runtime caps the run at 3 samples."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "artifacts").mkdir()
            spec = make_spec(run_dir / "src", run_dir / "product")
            spec["runtime"]["max_samples"] = 3
            (run_dir / "artifacts" / "agent_spec_resolved.json").write_text(
                json.dumps(spec), encoding="utf-8"
            )
            captured: dict[str, int] = {}

            def fake_run_agent(spec: dict, run_dir: Path, *, max_samples: int = 0) -> dict:
                captured["max_samples"] = max_samples
                return {"job_status": "succeeded"}

            argv = ["agent_runner.py", "--run-dir", str(run_dir), *extra]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                agent_runner, "run_agent", fake_run_agent
            ):
                self.assertEqual(agent_runner.main(), 0)
            return captured["max_samples"]

    def test_max_samples_defaults_to_the_resolved_plan(self) -> None:
        self.assertEqual(self.run_main(), 3)

    def test_max_samples_flag_overrides_the_resolved_plan(self) -> None:
        self.assertEqual(self.run_main("--max-samples", "1"), 1)

    def test_negative_max_samples_is_rejected(self) -> None:
        argv = ["agent_runner.py", "--run-dir", str(Path.cwd()), "--max-samples", "-1"]
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as ctx:
                agent_runner.main()
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--max-samples", stderr.getvalue())
        self.assertIn("negative", stderr.getvalue())


class ExtractTextTests(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(agent_runner.extract_text("  hello  "), "hello")

    def test_dict_fields(self) -> None:
        self.assertEqual(agent_runner.extract_text({"transcription": "abc"}), "abc")
        self.assertEqual(agent_runner.extract_text({"text": "abc"}), "abc")

    def test_unknown_payload(self) -> None:
        self.assertEqual(agent_runner.extract_text({"other": 1}), "")


if __name__ == "__main__":
    unittest.main()
