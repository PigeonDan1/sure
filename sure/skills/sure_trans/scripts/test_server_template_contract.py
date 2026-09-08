#!/usr/bin/env python3
from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
