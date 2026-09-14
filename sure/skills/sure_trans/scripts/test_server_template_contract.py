#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent


class ServerTemplateContractTests(unittest.TestCase):
    def test_server_loads_wrapper_once_without_model_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.py").write_text(
                "class ModelWrapper:\n"
                "    def __init__(self):\n"
                "        self.loaded = False\n"
                "    def load(self):\n"
                "        if self.loaded:\n"
                "            raise RuntimeError('load called twice')\n"
                "        self.loaded = True\n"
                "    def predict(self, input_data):\n"
                "        if not self.loaded:\n"
                "            raise RuntimeError('predict called before load')\n"
                "        return {'text': input_data['audio_path']}\n"
                "    def healthcheck(self):\n"
                "        return {'status': 'ok'}\n",
                encoding="utf-8",
            )
            template = (SCRIPTS_DIR / "templates" / "server.py").read_text(encoding="utf-8")
            rendered = template.replace('"__TOOL_NAME__"', '"transcribe_audio"').replace(
                "__INPUT_SCHEMA__",
                '{"type":"object","properties":{"audio_path":{"type":"string"}},"required":["audio_path"]}',
            )
            server = root / "server.py"
            server.write_text(rendered, encoding="utf-8")
            requests = (
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "transcribe_audio", "arguments": {"audio_path": "one.wav"}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "transcribe_audio", "arguments": {"audio_path": "two.wav"}},
                },
                {"jsonrpc": "2.0", "id": 4, "method": "shutdown"},
            )
            completed = subprocess.run(
                [sys.executable, str(server)],
                input="".join(json.dumps(request) + "\n" for request in requests),
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
                cwd=root,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
            self.assertEqual([response["id"] for response in responses], [1, 2, 3, 4])
            self.assertTrue(all("result" in response for response in responses))

    def test_mcp_smoke_accepts_speaker_embedding_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "speaker.wav"
            audio.write_bytes(b"RIFF-speaker")
            (root / "model.py").write_text(
                "class ModelWrapper:\n"
                "    def __init__(self): self.loaded = False\n"
                "    def load(self): self.loaded = True\n"
                "    def predict(self, input_data): return {'embedding': [0.1, -0.2, 0.3]}\n"
                "    def healthcheck(self): return {'status': 'ok'}\n",
                encoding="utf-8",
            )
            template = (SCRIPTS_DIR / "templates" / "server.py").read_text(encoding="utf-8")
            rendered = template.replace('"__TOOL_NAME__"', '"embed_speaker"').replace(
                "__INPUT_SCHEMA__",
                '{"type":"object","properties":{"audio_path":{"type":"string"}},"required":["audio_path"]}',
            )
            server = root / "server.py"
            server.write_text(rendered, encoding="utf-8")
            evidence = root / "mcp_smoke.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "mcp_smoke.py"),
                    "--audio",
                    str(audio),
                    "--tool",
                    "embed_speaker",
                    "--server-command",
                    sys.executable,
                    str(server),
                    "--produces",
                    str(evidence),
                    "--timeout",
                    "10",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
                cwd=root,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["tools_call"]["primary_field"], "embedding")
            self.assertEqual(payload["tools_call"]["embedding_dimension"], 3)

    def test_validate_template_requires_a_finite_numeric_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            contract = {
                "primary_field": "embedding",
                "required_fields": ["embedding"],
                "nonempty_fields": ["embedding"],
                "json_serializable": True,
            }
            template = (SCRIPTS_DIR / "templates" / "validate.py").read_text(encoding="utf-8")
            rendered = (
                template.replace("__MODEL_NAME__", "demo__speaker")
                .replace("__TASK_TYPE__", "SV")
                .replace(
                    "__IO_CONTRACT_JSON__",
                    json.dumps(contract, ensure_ascii=True, separators=(",", ":")),
                )
            )
            validate = root / "validate.py"
            validate.write_text(rendered, encoding="utf-8")
            environment = {**os.environ, "SURE_VALIDATE_ARTIFACTS_DIR": str(artifacts)}

            for embedding, expected_code in (([0.1, -0.2], 0), ([True], 1), ([float("nan")], 1)):
                with self.subTest(embedding=embedding):
                    (artifacts / "sample_output.json").write_text(
                        json.dumps({"embedding": embedding}) + "\n",
                        encoding="utf-8",
                    )
                    completed = subprocess.run(
                        [sys.executable, str(validate), "--stage", "contract"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=30,
                        cwd=root,
                        env=environment,
                    )
                    self.assertEqual(completed.returncode, expected_code)


if __name__ == "__main__":
    unittest.main()
