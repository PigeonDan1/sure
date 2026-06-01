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
        self._initialized = False
        self._wrapper: ModelWrapper | None = None
        self._tools = [
            {
                "name": "asr_transcribe",
                "description": "Transcribe speech to text using Kimi-Audio-7B-Instruct.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"audio_path": {"type": "string"}},
                    "required": ["audio_path"],
                },
            },
            {
                "name": "healthcheck",
                "description": "Return wrapper readiness and runtime metadata.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "s2tt_translate",
                "description": "Translate speech audio into target-language text using Kimi-Audio-7B-Instruct.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "audio_path": {"type": "string"},
                        "source_language": {"type": "string", "default": "auto"},
                        "target_language": {"type": "string", "default": "zh"},
                    },
                    "required": ["audio_path"],
                },
            },
            {
                "name": "recognize_emotion",
                "description": "Recognize speech emotion using Kimi-Audio-7B-Instruct.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"audio_path": {"type": "string"}},
                    "required": ["audio_path"],
                },
            },
            {
                "name": "recognize_gender",
                "description": "Recognize speaker gender using Kimi-Audio-7B-Instruct.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"audio_path": {"type": "string"}},
                    "required": ["audio_path"],
                },
            },
            {
                "name": "slu_understand",
                "description": "Answer an audio understanding prompt using Kimi-Audio-7B-Instruct.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "audio_path": {"type": "string"},
                        "prompt": {"type": "string"},
                    },
                    "required": ["audio_path"],
                },
            },
        ]

    def _get_wrapper(self) -> ModelWrapper:
        if self._wrapper is None:
            self._wrapper = ModelWrapper()
        return self._wrapper

    def handle_initialize(self, request: dict[str, Any]) -> dict[str, Any]:
        self._initialized = True
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "asr-kimi-audio-mcp-server", "version": "1.0.0"},
            },
        }

    def handle_tools_list(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._initialized:
            return self._error_response(request.get("id"), -32001, "Server not initialized")
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"tools": self._tools}}

    def handle_tools_call(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._initialized:
            return self._error_response(request.get("id"), -32001, "Server not initialized")
        params = request.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        request_id = request.get("id")
        try:
            if tool_name == "asr_transcribe":
                audio_path = arguments.get("audio_path")
                if not audio_path:
                    raise ValueError("audio_path is required")
                result = self._get_wrapper().predict(audio_path).to_dict()
            elif tool_name == "s2tt_translate":
                audio_path = arguments.get("audio_path")
                if not audio_path:
                    raise ValueError("audio_path is required")
                result = self._get_wrapper().translate(
                    audio_path,
                    source_language=arguments.get("source_language", "auto"),
                    target_language=arguments.get("target_language", "zh"),
                ).to_dict()
            elif tool_name == "recognize_emotion":
                audio_path = arguments.get("audio_path")
                if not audio_path:
                    raise ValueError("audio_path is required")
                result = self._get_wrapper().recognize_emotion(audio_path).to_dict()
            elif tool_name == "recognize_gender":
                audio_path = arguments.get("audio_path")
                if not audio_path:
                    raise ValueError("audio_path is required")
                result = self._get_wrapper().recognize_gender(audio_path).to_dict()
            elif tool_name == "slu_understand":
                audio_path = arguments.get("audio_path")
                if not audio_path:
                    raise ValueError("audio_path is required")
                result = self._get_wrapper().understand(
                    audio_path,
                    prompt=arguments.get("prompt"),
                ).to_dict()
            elif tool_name == "healthcheck":
                result = self._get_wrapper().healthcheck()
            else:
                raise ValueError(f"Unknown tool: {tool_name}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
            }
        except (InferenceError, ModelLoadError, ValueError) as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}

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
        return self._error_response(request.get("id"), -32601, f"Method not found: {method}")

    def _error_response(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

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
