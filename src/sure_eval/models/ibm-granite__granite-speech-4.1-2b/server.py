#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Any

from model import ModelWrapper


class MCPServer:
    def __init__(self) -> None:
        self._initialized = False
        self._model = ModelWrapper()

    def handle_initialize(self, request: dict[str, Any]) -> dict[str, Any]:
        self._initialized = True
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "granite-speech-4.1-2b",
                    "version": "1.0.0",
                },
            },
        }

    def handle_tools_list(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._initialized:
            return self._error(request.get("id"), -32001, "Server not initialized")
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "asr_transcribe",
                        "description": "Transcribe an audio file with IBM Granite Speech 4.1 2B.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"audio_path": {"type": "string"}},
                            "required": ["audio_path"],
                        },
                    }
                ]
            },
        }

    def handle_tools_call(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._initialized:
            return self._error(request.get("id"), -32001, "Server not initialized")
        params = request.get("params", {})
        if params.get("name") != "asr_transcribe":
            return self._error(request.get("id"), -32602, f"Unknown tool: {params.get('name')}")
        try:
            result = self._model.predict(params.get("arguments", {}))
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": result["text"]}],
                    "isError": False,
                    "metadata": result,
                },
            }
        except Exception as exc:  # noqa: BLE001 - JSON-RPC boundary
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                    "error": str(exc),
                },
            }

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        if method == "initialize":
            return self.handle_initialize(request)
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return self.handle_tools_list(request)
        if method == "tools/call":
            return self.handle_tools_call(request)
        if method == "shutdown":
            return {"jsonrpc": "2.0", "id": request.get("id"), "result": {}}
        return self._error(request.get("id"), -32601, f"Method not found: {method}")

    def _error(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def run(self) -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                response = self.handle_request(json.loads(line))
                if response is not None:
                    print(json.dumps(response, ensure_ascii=False), flush=True)
            except json.JSONDecodeError as exc:
                print(json.dumps(self._error(None, -32700, f"Parse error: {exc}")), flush=True)


if __name__ == "__main__":
    MCPServer().run()
