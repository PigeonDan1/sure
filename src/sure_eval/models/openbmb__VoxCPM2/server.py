"""Minimal JSON-RPC server for the VoxCPM2 wrapper."""

from __future__ import annotations

import json
import sys
from typing import Any

from model import ModelWrapper


class MCPServer:
    def __init__(self):
        self.model = ModelWrapper()

    def handle_initialize(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "openbmb__VoxCPM2", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            },
        }

    def handle_tools_list(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "tts",
                        "description": "Generate speech from text with VoxCPM2.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }
                ]
            },
        }

    def handle_tools_call(self, request: dict[str, Any]) -> dict[str, Any]:
        params = request.get("params") or {}
        arguments = params.get("arguments") or {}
        try:
            result = self.model.predict(arguments)
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {"content": [{"type": "text", "text": json.dumps(result.to_summary())}]},
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32000, "message": str(exc)},
            }


def main() -> int:
    server = MCPServer()
    handlers = {
        "initialize": server.handle_initialize,
        "tools/list": server.handle_tools_list,
        "tools/call": server.handle_tools_call,
    }
    for line in sys.stdin:
        try:
            request = json.loads(line)
            handler = handlers.get(request.get("method"))
            response = handler(request) if handler else {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32601, "message": "Method not found"},
            }
        except Exception as exc:  # noqa: BLE001
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        print(json.dumps(response), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
