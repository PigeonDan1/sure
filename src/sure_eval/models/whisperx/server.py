from __future__ import annotations

import json
import sys
from typing import Any

try:
    from .model import InferenceError, ModelLoadError, ModelWrapper
except ImportError:
    from model import InferenceError, ModelLoadError, ModelWrapper


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
                "serverInfo": {"name": "whisperx-mcp", "version": "1.0.0"},
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
                        "description": "Run WhisperX transcription on an audio file",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"audio_path": {"type": "string"}},
                            "required": ["audio_path"],
                        },
                    },
                    {
                        "name": "healthcheck",
                        "description": "Return wrapper readiness",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            },
        }

    def handle_tools_call(self, request: dict[str, Any]) -> dict[str, Any]:
        params = request.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {})

        try:
            if name == "transcribe_audio":
                audio_path = arguments.get("audio_path")
                if not audio_path:
                    raise ValueError("audio_path is required")
                payload = self._get_wrapper().transcribe(
                    audio_path,
                    language=arguments.get("language"),
                    return_timestamps=True,
                ).to_dict()
                return self._result_response(request.get("id"), payload)
            if name == "healthcheck":
                return self._result_response(request.get("id"), self._get_wrapper().healthcheck())
            return self._error_response(request.get("id"), -32601, f"Unknown tool: {name}")
        except (ModelLoadError, InferenceError, ValueError, FileNotFoundError) as exc:
            return self._error_response(request.get("id"), -32000, str(exc))

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
        if method == "shutdown":
            return {"jsonrpc": "2.0", "id": request.get("id"), "result": {}}
        return self._error_response(request.get("id"), -32601, f"Method not found: {method}")

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = self.handle_request(request)
                if response is not None:
                    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError as exc:
                sys.stdout.write(
                    json.dumps(
                        self._error_response(None, -32700, f"Parse error: {exc}"),
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                sys.stdout.flush()

    def _result_response(self, request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
            },
        }

    def _error_response(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    MCPServer().run()
