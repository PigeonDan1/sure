from __future__ import annotations

import json
import sys
from typing import Any

try:
    from .model import ConfigurationError, InferenceError, ModelLoadError, ModelWrapper
except ImportError:
    from model import ConfigurationError, InferenceError, ModelLoadError, ModelWrapper


class MCPServer:
    def __init__(self) -> None:
        self._wrapper: ModelWrapper | None = None

    def _get_wrapper(self) -> ModelWrapper:
        if self._wrapper is None:
            self._wrapper = ModelWrapper()
        return self._wrapper

    def handle_initialize(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "nvidia-parakeet-rnnt-1.1b-mcp-server",
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
                        "name": "transcribe_audio",
                        "description": "Transcribe a local audio file with nvidia/parakeet-rnnt-1.1b.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "audio_path": {"type": "string"},
                            },
                            "required": ["audio_path"],
                        },
                    },
                    {
                        "name": "healthcheck",
                        "description": "Return model wrapper readiness.",
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
            if tool_name == "transcribe_audio":
                result = self._get_wrapper().predict(arguments).to_dict()
            elif tool_name == "healthcheck":
                result = self._get_wrapper().healthcheck()
            else:
                raise ValueError(f"Unknown tool: {tool_name}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                    ]
                },
            }
        except (ConfigurationError, FileNotFoundError, InferenceError, ModelLoadError, ValueError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        if method == "initialize":
            return self.handle_initialize(request)
        if method == "tools/list":
            return self.handle_tools_list(request)
        if method == "tools/call":
            return self.handle_tools_call(request)
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
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    MCPServer().run()
