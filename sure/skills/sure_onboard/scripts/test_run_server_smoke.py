#!/usr/bin/env python3
"""The server smoke proves the declared framing, or says why it could not.

The gate hands the smoke a resolved protocol and arguments; this test hands it
fake servers instead and asserts what the evidence proves. The jsonl fake is
the important one: it reads the request line and refuses an MCP envelope, so a
smoke that sent one would fail here rather than at inference time.

Run directly:
    cd sure/skills/sure_onboard/scripts && python test_run_server_smoke.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SMOKE = HERE / "run_server_smoke.py"

# Two fake servers. Both print a log line to stdout before answering, because a
# real model server does too and the driver has to skip it.
MCP_FAKE = """
import json, sys
tool, payload = json.loads(sys.argv[1]), json.loads(sys.argv[2])
advertised = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
item = {"name": tool}
if advertised is not None:
    item["inputSchema"] = advertised
for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    rid, method = request.get("id"), request.get("method")
    print("loading model...", flush=True)
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05"}
    elif method == "tools/list":
        result = {"tools": [item]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    elif method == "shutdown":
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}), flush=True)
        break
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}), flush=True)
"""

JSONL_FAKE = """
import json, sys
payload = json.loads(sys.argv[1])
line = sys.stdin.readline()
request = json.loads(line)
assert "jsonrpc" not in request, "jsonl framing must not carry an MCP envelope"
assert request == payload, "jsonl framing must send the arguments as one flat object"
print("loading model...", flush=True)
print(json.dumps({"text": "ok", "echo": request}), flush=True)
"""

SILENT_FAKE = """
import sys, time
time.sleep(60)
"""

# Answers one jsonl request with the environment it was started in, so a test
# can see which variables reached it.
ENV_FAKE = """
import json, os, sys
json.loads(sys.stdin.readline())
print(json.dumps({
    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING", ""),
    "PYTHONUNBUFFERED": os.environ.get("PYTHONUNBUFFERED", ""),
}))
"""


class ServerSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.produces = self.tmp / "artifacts" / "server_smoke.json"

    def _fake(self, source: str, *args: object) -> list[str]:
        script = self.tmp / "fake_server.py"
        script.write_text(source, encoding="utf-8")
        return [sys.executable, str(script), *[json.dumps(item) for item in args]]

    def _run(self, *, protocol: str, command: list[str], tool: str = "enhance_speech",
             arguments: dict | None = None, timeout: float = 30.0,
             input_schema: dict | None = None, list_only: bool = False,
             env_json: dict | None = None, runner_env: dict | None = None,
             task: str = "") -> dict:
        invocation = [
            sys.executable, str(SMOKE),
            "--server-command", json.dumps(command),
            "--protocol", protocol,
            "--tool", tool,
            "--task", task,
            "--arguments-json", json.dumps(arguments if arguments is not None else {"audio_path": "/fixture/in.wav"}),
            "--timeout", str(timeout),
            "--produces", str(self.produces),
        ]
        if input_schema is not None:
            invocation.extend(["--input-schema-json", json.dumps(input_schema)])
        if env_json is not None:
            invocation.extend(["--env-json", json.dumps(env_json)])
        if list_only:
            invocation.append("--list-only")
        result = subprocess.run(invocation, capture_output=True, text=True,
                                env={**os.environ, **(runner_env or {})})
        self.assertTrue(self.produces.is_file(), "the smoke must always write evidence")
        evidence = json.loads(self.produces.read_text(encoding="utf-8"))
        evidence["_returncode"] = result.returncode
        evidence["_stderr"] = result.stderr
        return evidence

    def test_a_mcp_server_that_answers_every_step_passes(self) -> None:
        audio = self.tmp / "out.wav"
        audio.write_bytes(b"RIFF")
        evidence = self._run(
            protocol="mcp",
            command=self._fake(MCP_FAKE, "enhance_speech", {"audio_path": str(audio)}),
        )
        self.assertEqual(evidence["_returncode"], 0)
        self.assertEqual(evidence["status"], "passed")
        for step in ("initialize", "tools_list", "tools_call", "shutdown"):
            self.assertTrue(evidence["steps"][step]["ok"], step)
        self.assertEqual(evidence["stdout_junk_count"], 4)

    def test_a_mcp_server_that_names_a_path_it_never_wrote_fails(self) -> None:
        evidence = self._run(
            protocol="mcp",
            command=self._fake(MCP_FAKE, "enhance_speech", {"audio_path": str(self.tmp / "gone.wav")}),
        )
        self.assertEqual(evidence["_returncode"], 1)
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("does not exist", evidence["error"])

    def test_a_wrong_tool_name_fails_at_tools_list(self) -> None:
        evidence = self._run(
            protocol="mcp",
            command=self._fake(MCP_FAKE, "enhance_speech", {"text": "hi"}),
            tool="other_tool",
        )
        self.assertEqual(evidence["_returncode"], 1)
        self.assertIn("tools/list failed", evidence["error"])

    def test_a_jsonl_server_reads_one_flat_request_line(self) -> None:
        arguments = {"audio_path": "/fixture/in.wav"}
        evidence = self._run(
            protocol="jsonl",
            command=self._fake(JSONL_FAKE, arguments),
            arguments=arguments,
        )
        self.assertEqual(evidence["_returncode"], 0)
        self.assertEqual(evidence["status"], "passed")
        self.assertTrue(evidence["steps"]["single_request"]["ok"])
        self.assertNotIn("initialize", evidence["steps"])

    def test_jsonl_nested_result_with_text_fails_asr_projection(self) -> None:
        nested = {"ok": True, "result": {"text": "نص عربي"}}
        source = 'import json, sys\nsys.stdin.readline()\nprint(json.dumps(' + repr(nested) + '))\n'
        evidence = self._run(protocol="jsonl", command=self._fake(source), task="ASR")
        self.assertEqual(evidence["_returncode"], 1)
        self.assertIn("must provide text", evidence["error"])
        self.assertNotIn("prediction", evidence["steps"]["single_request"])
        self.assertIn("نص عربي", evidence["steps"]["single_request"]["primary_output"])

    def test_jsonl_explicit_empty_asr_text_passes(self) -> None:
        source = 'import json, sys\nsys.stdin.readline()\nprint(json.dumps({"text": ""}))\n'
        evidence = self._run(protocol="jsonl", command=self._fake(source), task="ASR")
        self.assertEqual(evidence["_returncode"], 0, evidence.get("error"))
        self.assertEqual(evidence["steps"]["single_request"]["projection"], "")
        self.assertEqual(evidence["steps"]["single_request"]["prediction"], {"text": ""})

    def test_jsonl_top_level_text_passes_asr_projection(self) -> None:
        source = 'import json, sys\nsys.stdin.readline()\nprint(json.dumps({"text": "نص عربي"}))\n'
        evidence = self._run(protocol="jsonl", command=self._fake(source), task="ASR")
        self.assertEqual(evidence["_returncode"], 0)
        self.assertEqual(evidence["steps"]["single_request"]["projection"], "نص عربي")

    def test_both_protocols_accept_standard_fields_and_legitimate_zero_values(self) -> None:
        audio = self.tmp / "generated.wav"
        audio.write_bytes(b"RIFF")
        cases = [
            ("ASR", {"text": ""}, {"text": ""}),
            ("S2TT", {"translation": "translated text"}, {"translation": "translated text", "text": "translated text"}),
            ("SLU", {"answer": "activate_lights"}, {"answer": "activate_lights", "text": "activate_lights"}),
            ("S2TT", "translated text", {"translation": "translated text", "text": "translated text"}),
            ("SLU", "activate_lights", {"answer": "activate_lights", "text": "activate_lights"}),
            ("speech-enhancement", {"audio_path": str(audio)}, {"audio_path": str(audio), "enhanced_audio": str(audio)}),
            ("DOA", {"sources": []}, {"sources": []}),
            ("SV", {"embedding": [0, 0.5]}, {"embedding": [0, 0.5]}),
            ("CLASSIFICATION", {"label": 0}, {"label": "0"}),
            ("KWS", {"detected": False, "score": 0}, {"detected": False, "score": 0.0, "keyword": None}),
            ("VAD", {"speech_segments": []}, {"speech_segments": []}),
        ]
        for protocol in ("mcp", "jsonl"):
            for task, payload, expected in cases:
                with self.subTest(protocol=protocol, task=task):
                    if protocol == "mcp":
                        command = self._fake(MCP_FAKE, "predict", payload)
                    else:
                        command = self._fake(
                            'import json, sys\nsys.stdin.readline()\nprint(json.dumps(' + repr(payload) + '))\n'
                        )
                    evidence = self._run(protocol=protocol, command=command, tool="predict", task=task)
                    self.assertEqual(evidence["_returncode"], 0, evidence.get("error"))
                    step = "tools_call" if protocol == "mcp" else "single_request"
                    self.assertEqual(evidence["steps"][step]["prediction"], expected)

    def test_both_protocols_reject_missing_or_empty_sv_embeddings(self) -> None:
        for protocol in ("mcp", "jsonl"):
            for payload in ({"ok": True}, {"embedding": []}):
                with self.subTest(protocol=protocol, payload=payload):
                    command = self._fake(MCP_FAKE, "embed_speaker", payload) if protocol == "mcp" else self._fake(
                        'import json, sys\nsys.stdin.readline()\nprint(json.dumps(' + repr(payload) + '))\n'
                    )
                    evidence = self._run(protocol=protocol, command=command, tool="embed_speaker", task="SV")
                    self.assertEqual(evidence["_returncode"], 1)
                    self.assertIn("embedding", evidence["error"])

    def test_mcp_nested_result_with_text_fails_asr_projection(self) -> None:
        evidence = self._run(
            protocol="mcp", task="ASR",
            command=self._fake(MCP_FAKE, "enhance_speech", {"ok": True, "result": {"text": "نص عربي"}}),
        )
        self.assertEqual(evidence["_returncode"], 1)
        self.assertIn("must provide text", evidence["error"])

    def test_a_declared_env_reaches_the_server_over_the_sanitized_child_env(self) -> None:
        # The infer client starts the model server from the sanitized child env
        # and layers `server.env` over it, so a declared `PYTHONPATH` pointing
        # into the harness runtime still reaches the server. The runner has to
        # layer it the same way round: sanitizing last would strip the model's
        # own declaration as if it were harness interpreter state.
        root = self.tmp / "harness-runtime"
        (root / "declared-lib").mkdir(parents=True)
        declared = str(root / "declared-lib")
        evidence = self._run(
            protocol="jsonl",
            command=self._fake(ENV_FAKE),
            env_json={"PYTHONPATH": declared},
            runner_env={"SURE_HARNESS_RUNTIME_ROOT": str(root), "PYTHONPATH": declared},
        )
        self.assertEqual(evidence["_returncode"], 0)
        seen = json.loads(evidence["steps"]["single_request"]["primary_output"])
        self.assertEqual(seen["PYTHONPATH"], declared)
        # And the encoding the client adds itself, for a server that would
        # otherwise write its output under the console code page.
        self.assertEqual(seen["PYTHONIOENCODING"], "utf-8")

    def test_the_runner_sanitizes_the_harness_pythonpath_it_inherits(self) -> None:
        # Pins the case above from the other side: with nothing declared, a
        # `PYTHONPATH` the harness runtime exported for itself must not reach
        # the model's server.
        root = self.tmp / "harness-runtime"
        (root / "runtime-lib").mkdir(parents=True)
        evidence = self._run(
            protocol="jsonl",
            command=self._fake(ENV_FAKE),
            runner_env={"SURE_HARNESS_RUNTIME_ROOT": str(root), "PYTHONPATH": str(root / "runtime-lib")},
        )
        self.assertEqual(evidence["_returncode"], 0)
        seen = json.loads(evidence["steps"]["single_request"]["primary_output"])
        self.assertEqual(seen["PYTHONPATH"], "")

    def test_a_silent_server_fails_within_the_deadline(self) -> None:
        evidence = self._run(
            protocol="jsonl",
            command=self._fake(SILENT_FAKE),
            timeout=2.0,
        )
        self.assertEqual(evidence["_returncode"], 1)
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("no JSON response line", evidence["error"])

    def test_an_unstartable_server_records_the_failure(self) -> None:
        evidence = self._run(protocol="mcp", command=[str(self.tmp / "no-such-binary")])
        self.assertEqual(evidence["_returncode"], 1)
        self.assertIn("cannot start the declared server", evidence["error"])


    def test_a_server_that_advertises_the_declared_contract_passes(self) -> None:
        audio = self.tmp / "out.wav"
        audio.write_bytes(b"RIFF")
        schema = {"type": "object", "properties": {"audio_path": {"type": "string"}}, "required": ["audio_path"]}
        evidence = self._run(
            protocol="mcp",
            command=self._fake(MCP_FAKE, "enhance_speech", {"audio_path": str(audio)}, schema),
            input_schema=schema,
        )
        self.assertEqual(evidence["_returncode"], 0)
        steps = evidence["steps"]["tools_list"]
        self.assertEqual(steps["advertised_contract"], steps["expected_contract"])
        self.assertEqual(steps["expected_contract"]["properties"], ["audio_path"])

    def test_a_server_that_advertises_another_contract_fails(self) -> None:
        # Same tool name, different arguments: the config and the server disagree
        # about what may be sent, so calling it is a coin flip.
        audio = self.tmp / "out.wav"
        audio.write_bytes(b"RIFF")
        declared = {"type": "object", "properties": {"audio_path": {"type": "string"}}, "required": ["audio_path"]}
        advertised = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
        evidence = self._run(
            protocol="mcp",
            command=self._fake(MCP_FAKE, "enhance_speech", {"audio_path": str(audio)}, advertised),
            input_schema=declared,
        )
        self.assertEqual(evidence["_returncode"], 1)
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("advertises a different contract", evidence["error"])

    def test_a_server_that_advertises_no_contract_fails_when_one_is_declared(self) -> None:
        evidence = self._run(
            protocol="mcp",
            command=self._fake(MCP_FAKE, "enhance_speech", {"text": "hi"}),
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        )
        self.assertEqual(evidence["_returncode"], 1)
        self.assertIn("advertises a different contract", evidence["error"])

    def test_list_only_stops_before_calling_the_tool(self) -> None:
        schema = {"type": "object", "properties": {"audio_path": {"type": "string"}}}
        evidence = self._run(
            protocol="mcp",
            command=self._fake(MCP_FAKE, "enhance_speech", {"audio_path": "/fixture/in.wav"}, schema),
            input_schema=schema,
            list_only=True,
        )
        self.assertEqual(evidence["_returncode"], 0)
        self.assertEqual(evidence["steps"]["tools_call"]["skipped"], "list_only")
        self.assertTrue(evidence["steps"]["shutdown"]["ok"])
        self.assertTrue(evidence["list_only"])

    def test_a_jsonl_server_has_no_tool_list_to_stop_at(self) -> None:
        evidence = self._run(
            protocol="jsonl",
            command=self._fake(JSONL_FAKE, {"audio_path": "/fixture/in.wav"}),
            list_only=True,
        )
        self.assertEqual(evidence["_returncode"], 1)
        self.assertIn("--list-only", evidence["error"])


if __name__ == "__main__":
    unittest.main()
