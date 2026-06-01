from __future__ import annotations

import json
import sys
from typing import Any

try:
    from .model import InferenceError, ModelLoadError, S2TTNLLBModel
except ImportError:
    from model import InferenceError, ModelLoadError, S2TTNLLBModel


class MCPServer:
    def __init__(self) -> None:
        self._wrapper: S2TTNLLBModel | None = None

    def _get_wrapper(self) -> S2TTNLLBModel:
        if self._wrapper is None:
            self._wrapper = S2TTNLLBModel()
        return self._wrapper

    def handle_initialize(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "s2tt-nllb-mcp-server",
                    "version": "1.0.0",
                },
            },
        }

    def handle_tools_list(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "s2tt_translate",
                        "description": "Run cascaded Whisper ASR plus NLLB translation on a local audio path.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "audio_path": {"type": "string"},
                                "source_lang": {"type": "string"},
                                "target_lang": {"type": "string"},
                            },
                            "required": ["audio_path", "source_lang", "target_lang"],
                        },
                    },
                    {
                        "name": "healthcheck",
                        "description": "Return wrapper readiness and runtime metadata.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            },
        }

    def handle_tools_call(self, request: dict[str, Any]) -> dict[str, Any]:
        params = request.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        request_id = request.get("id")
        try:
            if tool_name == "s2tt_translate":
                result = self._get_wrapper().predict(arguments)
            elif tool_name == "healthcheck":
                result = self._get_wrapper().healthcheck()
            else:
                raise ValueError(f"Unknown tool: {tool_name}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                },
            }
        except (InferenceError, ModelLoadError, ValueError, FileNotFoundError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        if method == "initialize":
            return self.handle_initialize(request)
        if method == "tools/list":
            return self.handle_tools_list(request)
        if method == "tools/call":
            return self.handle_tools_call(request)
        if method == "notifications/initialized":
            return None
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            response = self.handle_request(json.loads(line))
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    MCPServer().run()
