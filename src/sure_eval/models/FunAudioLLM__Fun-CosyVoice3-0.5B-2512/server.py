#!/usr/bin/env python3
"""Minimal JSON-RPC server for Fun-CosyVoice3 zero-shot TTS."""

from __future__ import annotations

import json
import sys
from typing import Any


class MCPServer:
    def __init__(self) -> None:
        self._model = None
        self._tools = [
            {
                "name": "tts_zero_shot",
                "description": "Run Fun-CosyVoice3 zero-shot text-to-speech.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "prompt_text": {"type": "string"},
                        "prompt_audio_path": {"type": "string"},
                    },
                    "required": ["text", "prompt_text", "prompt_audio_path"],
                },
            }
        ]

    def _load_model(self):
        if self._model is None:
            from model import ModelWrapper

            self._model = ModelWrapper()
        return self._model

    def _handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fun-cosyvoice3-server", "version": "0.1.0"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self._tools}}
        if method == "tools/call":
            arguments = request.get("params", {}).get("arguments", {})
            try:
                result = self._load_model().predict(arguments).to_dict()
                return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [], "raw": result}}
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
                }
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": str(method)}}

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                response = self._handle_request(json.loads(line))
            except Exception as exc:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(exc)}}
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    MCPServer().run()
