"""Minimal JSON-RPC server for the dots.tts-base wrapper."""

from __future__ import annotations

import json
import sys
from typing import Any

from model import ModelWrapper


class MCPServer:
    def __init__(self) -> None:
        self.model = ModelWrapper()

    def handle_initialize(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "dots.tts-base"}},
        }

    def handle_tools_list(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "synthesize_speech",
                        "description": "Generate speech from text using dots.tts-base.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "prompt_audio_path": {"type": "string"},
                                "prompt_text": {"type": "string"},
                                "output_path": {"type": "string"},
                            },
                            "required": ["text", "prompt_audio_path"],
                        },
                    }
                ]
            },
        }

    def handle_tools_call(self, request: dict[str, Any]) -> dict[str, Any]:
        params = request.get("params") or {}
        result = self.model.predict(params.get("arguments") or params)
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": result.to_dict()}

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        try:
            if method == "initialize":
                return self.handle_initialize(request)
            if method == "tools/list":
                return self.handle_tools_list(request)
            if method == "tools/call":
                return self.handle_tools_call(request)
            return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32601, "message": "Method not found"}}
        except Exception as exc:
            print(f"dots.tts-base server error: {exc}", file=sys.stderr)
            return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32000, "message": str(exc)}}


def main() -> int:
    server = MCPServer()
    for line in sys.stdin:
        if not line.strip():
            continue
        response = server.handle(json.loads(line))
        print(json.dumps(response), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
