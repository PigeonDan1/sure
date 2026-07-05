#!/usr/bin/env python3
"""Minimal MCP-style server for Qwen3-TTS 1.7B Base."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


class MCPServer:
    def __init__(self) -> None:
        self._model = None
        self._tools = [
            {
                "name": "generate_voice_clone",
                "description": "Generate speech from text using a reference voice audio.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "language": {"type": "string", "default": "English"},
                        "ref_audio": {"type": "string"},
                        "x_vector_only_mode": {"type": "boolean", "default": True},
                        "max_new_tokens": {"type": "integer", "default": 128},
                    },
                    "required": ["text", "ref_audio"],
                },
            }
        ]

    def _load_model(self):
        if self._model is None:
            from model import ModelWrapper

            self._model = ModelWrapper({"model_path": os.environ.get("MODEL_PATH")})
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
                    "serverInfo": {"name": "qwen3-tts-1-7b-base-server", "version": "0.1.0"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self._tools}}
        if method == "tools/call":
            params = request.get("params", {})
            arguments = params.get("arguments", {})
            try:
                result = self._load_model().predict(arguments).to_dict()
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=True)}], "raw": result},
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True},
                }
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": str(method)}}

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                response = self._handle_request(json.loads(line))
            except Exception as exc:  # noqa: BLE001
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(exc)}}
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    MCPServer().run()
