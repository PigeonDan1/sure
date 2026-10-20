#!/usr/bin/env python3
"""VALIDATE_INFER drives the model's declared server in the declared framing.

In-process inference proves the model, not the server. A model that ships
`server.py` has to answer the protocol its `config.yaml` declares, and the gate
that says so has to run before the model can be approved -- which is what these
cases pin: the declaration is read, a silent default is refused, and the gate
fails when the server answers in another framing.

Run directly:
    cd sure/skills/sure_onboard/scripts && python test_run_validate_server_smoke.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_validate
from sure.runtime.uvenv import runtime_python_relative

HERE = Path(__file__).resolve().parent

# An MCP server that echoes what it was handed and what it was started with, so
# the evidence shows both the fixture input that reached the tool and the launch
# environment the gate reproduced. Its tools/list must advertise SERVER_CONFIG's
# contract: the gate now compares the two, and a fake that under-reports would
# fail for the drift rather than for the behaviour under test.
ECHO_FAKE = """
import json, os, sys
SCHEMA = {
    "type": "object",
    "properties": {"noisy_audio": {"type": "string"}, "audio_path": {"type": "string"}},
    "required": ["audio_path"],
}
for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    rid, method = request.get("id"), request.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05"}
    elif method == "tools/list":
        result = {"tools": [{"name": "enhance_speech", "inputSchema": SCHEMA}]}
    elif method == "tools/call":
        arguments = (request.get("params") or {}).get("arguments") or {}
        result = {"content": [{"type": "text", "text": json.dumps({
            "audio_path": arguments.get("audio_path", ""),
            "MODEL_PATH": os.environ.get("MODEL_PATH", ""),
            "cwd": os.getcwd(),
        })}]}
    elif method == "shutdown":
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}), flush=True)
        break
    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}), flush=True)
"""


def fake_server_source(*, notify: bool) -> str:
    """The echo server, optionally announcing progress before every answer."""
    if not notify:
        return ECHO_FAKE
    line = (
        '    print(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",'
        ' "params": {"progress": 0.5}}), flush=True)\n'
    )
    return ECHO_FAKE.replace("    request = json.loads(line)", line + "    request = json.loads(line)", 1)


SERVER_CONFIG = """name: demo
model:
  id: demo/model
  task: SE
server:
  command: [python, server.py]
{declared}tools:
  - name: enhance_speech
    input_schema:
      type: object
      required: [audio_path]
      properties:
        noisy_audio:
          type: string
        audio_path:
          type: string
"""


class DeclarationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.run_dir = self.tmp / ".sure" / "runs" / "run1"
        (self.run_dir / "artifacts").mkdir(parents=True)
        self.sample = self.run_dir / "artifacts" / "noisy.wav"
        self.sample.write_bytes(b"RIFF")

    def _model_dir(self, *, declared: str = "  protocol: mcp\n", command: str = "[python, server.py]") -> Path:
        model_dir = self.tmp / "sure" / "models" / "demo"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "config.yaml").write_text(
            SERVER_CONFIG.format(declared=declared if command else "").replace(
                "command: [python, server.py]", f"command: {command}"
            ),
            encoding="utf-8",
        )
        (self.run_dir / "artifacts" / "fixture_manifest.json").write_text(
            json.dumps({
                "model_dir": str(model_dir),
                "samples": [{
                    "key": "s1",
                    "audio_path": str(self.sample),
                    "audio_roles": {"audio_path": str(self.sample)},
                }],
            }),
            encoding="utf-8",
        )
        return model_dir

    def test_a_declared_server_reports_the_framing_its_config_declares(self) -> None:
        declaration = run_validate.server_declaration(self._model_dir(declared="  protocol: jsonl\n"), self.run_dir)
        assert declaration is not None
        self.assertEqual(declaration["protocol"], "jsonl")
        self.assertEqual(declaration["tool"], "enhance_speech")
        self.assertEqual(declaration["task"], "SE")
        self.assertEqual(declaration["command"], ["python", "server.py"])

    def test_an_alias_resolves_to_the_same_framing_the_client_uses(self) -> None:
        declaration = run_validate.server_declaration(self._model_dir(declared="  mode: stdio_jsonrpc\n"), self.run_dir)
        assert declaration is not None
        self.assertEqual(declaration["protocol"], "mcp")

    def test_classification_supplies_task_when_config_omits_it(self) -> None:
        model_dir = self._model_dir()
        config = model_dir / "config.yaml"
        config.write_text(config.read_text(encoding="utf-8").replace("  task: SE\n", ""), encoding="utf-8")
        (self.run_dir / "artifacts" / "classification.json").write_text(
            json.dumps({"task_type": "tts"}), encoding="utf-8"
        )
        declaration = run_validate.server_declaration(model_dir, self.run_dir)
        assert declaration is not None
        self.assertEqual(declaration["task"], "TTS")

    def test_the_shorthand_interpreter_is_swapped_for_the_model_venv(self) -> None:
        # sure_feed scaffolds `command: [".venv/bin/python", "server.py"]`, and by
        # validation time the model's venv is built. Starting the shorthand
        # verbatim judges a process inference would never run: on Windows a
        # `.venv/bin/python` this platform's venv never made, on Linux a PATH
        # interpreter without the model's dependencies.
        model_dir = self._model_dir(command="[.venv/bin/python, server.py]")
        local_python = model_dir / ".venv" / runtime_python_relative()
        local_python.parent.mkdir(parents=True)
        local_python.write_text("", encoding="utf-8")

        declaration = run_validate.server_declaration(model_dir, self.run_dir)

        assert declaration is not None
        self.assertEqual(declaration["command"], [str(local_python), "server.py"])

    def test_a_shorthand_interpreter_with_no_venv_behind_it_is_left_alone(self) -> None:
        # Pins the case above: the swap substitutes the venv's interpreter when
        # there is one, it does not rewrite what the config spells.
        model_dir = self._model_dir(command="[.venv/bin/python, server.py]")

        declaration = run_validate.server_declaration(model_dir, self.run_dir)

        assert declaration is not None
        self.assertEqual(declaration["command"], [".venv/bin/python", "server.py"])

    def test_a_declared_server_must_say_which_framing_it_speaks(self) -> None:
        with self.assertRaisesRegex(ValueError, "no server.protocol"):
            run_validate.server_declaration(self._model_dir(declared=""), self.run_dir)

    def test_no_declared_command_leaves_the_server_to_the_harness_wrapper(self) -> None:
        self.assertIsNone(run_validate.server_declaration(self._model_dir(declared="", command=""), self.run_dir))

    def test_the_fixture_input_is_the_argument_the_declared_field_names(self) -> None:
        model_dir = self._model_dir()
        declaration = run_validate.server_declaration(model_dir, self.run_dir)
        assert declaration is not None
        # The tool requires audio_path and the fixture records it under that same
        # role, so the smoke drives the tool with the input it asks for.
        self.assertEqual(declaration["arguments"], {"audio_path": str(self.sample)})

    def test_a_required_input_the_fixture_cannot_fill_is_refused(self) -> None:
        # The fixture records audio_path; this tool requires noisy_audio. Driving
        # the call anyway sends it without the one field its own contract calls
        # mandatory, and a lenient answer would carry the gate on a call that
        # proved nothing. The escape hatch is server_smoke_input.json.
        model_dir = self._model_dir()
        (model_dir / "config.yaml").write_text(
            SERVER_CONFIG.format(declared="  protocol: mcp\n").replace(
                "required: [audio_path]", "required: [noisy_audio]"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "required tool input"):
            run_validate.server_declaration(model_dir, self.run_dir)

    def test_an_authored_argument_file_overrides_the_fixture_input(self) -> None:
        model_dir = self._model_dir()
        authored = self.run_dir / "artifacts" / "server_smoke_input.json"
        authored.write_text(json.dumps({"arguments": {"text": "hello"}}), encoding="utf-8")
        declaration = run_validate.server_declaration(model_dir, self.run_dir)
        assert declaration is not None
        self.assertEqual(declaration["arguments"], {"text": "hello"})


    def test_the_declaration_carries_the_contract_the_smoke_must_match(self) -> None:
        declaration = run_validate.server_declaration(self._model_dir(), self.run_dir)
        assert declaration is not None
        # The smoke compares this against what the server advertises on tools/list.
        self.assertEqual(
            sorted(declaration["input_schema"]["properties"]), ["audio_path", "noisy_audio"]
        )

    def test_a_declared_tool_no_client_can_call_is_refused(self) -> None:
        # The FireRedVAD shape: a tool with a name and a description and nothing
        # else. /sure_infer filters the arguments it sends against
        # input_schema.properties, so this tool cannot be called at all -- the
        # gate is where that has to be said, not inference on an approved bundle.
        model_dir = self.tmp / "sure" / "models" / "vad"
        model_dir.mkdir(parents=True)
        (model_dir / "config.yaml").write_text(
            "name: vad\n"
            "server:\n"
            "  command: [python, server.py]\n"
            "  protocol: mcp\n"
            "tools:\n"
            "  - name: vad\n"
            "    description: Voice activity detection returning speech_segments.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "no input_schema mapping"):
            run_validate.server_declaration(model_dir, self.run_dir)


def stage_infer_gate(scratch: Path, declared: str, *, notify: bool = False) -> tuple[Path, Path, Path]:
    """Lay out a model whose declared server really answers MCP."""
    model_dir = scratch / "sure" / "models" / "demo"
    artifacts = model_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "fixture").mkdir()
    wav = artifacts / "noisy.wav"
    wav.write_bytes(b"RIFF")
    (model_dir / "model.py").write_text("class ModelWrapper:\n    pass\n", encoding="utf-8")
    (model_dir / "sub").mkdir()
    server = scratch / "fake_server.py"
    server.write_text(fake_server_source(notify=notify), encoding="utf-8")
    (model_dir / "config.yaml").write_text(
        SERVER_CONFIG.format(declared=declared).replace(
            "[python, server.py]",
            json.dumps([sys.executable, str(server)]),
        ),
        encoding="utf-8",
    )
    run_dir = scratch / ".sure" / "runs" / "run1"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "artifacts" / "fixture_manifest.json").write_text(
        json.dumps({"model_dir": str(model_dir), "samples": [
            {"key": "s1", "audio_path": str(wav), "audio_roles": {"audio_path": str(wav)}}]}),
        encoding="utf-8",
    )
    (run_dir / "artifacts" / "sample_output.json").write_text(json.dumps({"audio_path": str(wav)}), encoding="utf-8")
    produces = run_dir / "artifacts" / "infer_result.json"
    produces.write_text(json.dumps({
        "infer_passed": True, "model_dir": str(model_dir),
        "run_command": [sys.executable, "-c", "print('infer ok')"],
    }), encoding="utf-8")
    return model_dir, run_dir, produces


def run_gate(run_dir: Path, produces: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HERE / "run_validate.py"), "--kind", "infer",
         "--run-dir", str(run_dir), "--produces", str(produces)],
        capture_output=True, text=True, cwd=str(HERE),
    )


class GateTests(unittest.TestCase):
    """The whole gate, on a model whose server.py really answers MCP."""

    def test_gate_uses_standard_task_outputs_and_rejects_empty_sv(self) -> None:
        cases = [
            ("S2TT", {"translation": "translated text"}, True),
            ("SLU", {"answer": "activate_lights"}, True),
            ("DOA", {"sources": [{"azimuth": 30, "activity": True}]}, True),
            ("SV", {"embedding": [0, 0.5]}, True),
            ("SV", {"ok": True}, False),
            ("SV", {"embedding": []}, False),
        ]
        for task, payload, passed in cases:
            with self.subTest(task=task, payload=payload), tempfile.TemporaryDirectory() as tmp:
                scratch = Path(tmp)
                model_dir, run_dir, produces = stage_infer_gate(scratch, "  protocol: jsonl\n")
                config = model_dir / "config.yaml"
                config.write_text(config.read_text().replace("task: SE", f"task: {task}"), encoding="utf-8")
                (scratch / "fake_server.py").write_text(
                    'import json, sys\nsys.stdin.readline()\nprint(json.dumps(' + repr(payload) + '), flush=True)\n',
                    encoding="utf-8",
                )
                result = run_gate(run_dir, produces)
                self.assertEqual(result.returncode == 0, passed, result.stderr + result.stdout)
                evidence = json.loads((run_dir / "artifacts" / "server_smoke.json").read_text(encoding="utf-8"))
                self.assertEqual(evidence["status"], "passed" if passed else "failed")
                self.assertEqual(evidence["task"], task)

    def test_the_gate_rejects_jsonl_asr_with_an_unprojectable_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            model_dir, run_dir, produces = stage_infer_gate(scratch, "  protocol: jsonl\n")
            config = model_dir / "config.yaml"
            config.write_text(config.read_text(encoding="utf-8").replace("task: SE", "task: ASR"), encoding="utf-8")
            (scratch / "fake_server.py").write_text(
                'import json, sys\nsys.stdin.readline()\n'
                'print(json.dumps({"ok": True, "result": {"text": "نص عربي"}}), flush=True)\n',
                encoding="utf-8",
            )
            result = run_gate(run_dir, produces)

            self.assertNotEqual(result.returncode, 0)
            evidence = json.loads((run_dir / "artifacts" / "server_smoke.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["task"], "ASR")
            self.assertIn("prediction projection is empty", evidence["error"])
            self.assertEqual(evidence["steps"]["single_request"]["prediction"], {"text": ""})

    def test_the_gate_passes_when_the_declared_server_answers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            model_dir, run_dir, produces = stage_infer_gate(scratch, "  protocol: mcp\n")
            result = run_gate(run_dir, produces)

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("server_smoke=passed", result.stdout)
            evidence = json.loads((run_dir / "artifacts" / "server_smoke.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["status"], "passed")
            self.assertEqual(evidence["protocol"], "mcp")
            self.assertTrue(evidence["steps"]["tools_call"]["output_nonempty"])
            self.assertEqual(evidence["steps"]["tools_call"]["prediction"]["audio_path"], str(model_dir / "artifacts" / "noisy.wav"))
            self.assertIn("noisy.wav", evidence["steps"]["tools_call"]["primary_output"])

    def test_the_gate_starts_the_server_with_the_declared_env_and_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            model_dir, run_dir, produces = stage_infer_gate(
                scratch,
                "  protocol: mcp\n  working_dir: sub\n  env:\n    MODEL_PATH: /models/demo\n",
            )
            result = run_gate(run_dir, produces)

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            output = json.loads(
                json.loads((run_dir / "artifacts" / "server_smoke.json").read_text(encoding="utf-8"))
                ["steps"]["tools_call"]["primary_output"]
            )
            # A wrapper that loads from MODEL_PATH and resolves weights against
            # `.` is only tested if the gate hands it the same env and cwd the
            # infer client would.
            self.assertEqual(output["MODEL_PATH"], "/models/demo")
            self.assertEqual(Path(output["cwd"]).resolve(), (model_dir / "sub").resolve())


    def test_a_server_that_announces_progress_before_answering_still_passes(self) -> None:
        # The infer client reads past a server's progress notifications to find
        # the response to its own request. A smoke that reads only the next line
        # fails a server that inference would have driven without complaint.
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            _, run_dir, produces = stage_infer_gate(scratch, "  protocol: mcp\n", notify=True)
            result = run_gate(run_dir, produces)

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            evidence = json.loads((run_dir / "artifacts" / "server_smoke.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["status"], "passed")
            self.assertTrue(evidence["steps"]["tools_call"]["output_nonempty"])


    def test_the_gate_refuses_a_tool_no_client_can_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            model_dir, run_dir, produces = stage_infer_gate(scratch, "  protocol: mcp\n")
            (model_dir / "config.yaml").write_text(
                "name: demo\n"
                "server:\n"
                f"  command: {json.dumps([sys.executable, str(scratch / 'fake_server.py')])}\n"
                "  protocol: mcp\n"
                "tools:\n"
                "  - name: enhance_speech\n"
                "    description: Enhance speech for an input audio_path.\n",
                encoding="utf-8",
            )
            result = run_gate(run_dir, produces)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no input_schema mapping", result.stderr + result.stdout)
            # The gate refused before the server was ever driven, so no evidence
            # was written claiming otherwise.
            self.assertFalse((run_dir / "artifacts" / "server_smoke.json").is_file())


if __name__ == "__main__":
    unittest.main()
