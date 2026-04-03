from __future__ import annotations

import json
import logging
import sys
from typing import Any

try:
    from .model import ModelWrapper, contract_result_to_json
except ImportError:
    from model import ModelWrapper, contract_result_to_json


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


class MCPServer:
    def __init__(self) -> None:
        self._wrapper: ModelWrapper | None = None

    def _wrapper_instance(self) -> ModelWrapper:
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
                "serverInfo": {"name": "wespeaker-mcp", "version": "1.0.0"},
            },
        }

    def handle_tools_list(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "speaker_verify",
                        "description": "Run the validated WeSpeaker english similarity path on an enrollment/trial audio pair.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "enrollment_audio": {"type": "string"},
                                "trial_audio": {"type": "string"},
                            },
                            "required": ["enrollment_audio", "trial_audio"],
                        },
                    },
                    {
                        "name": "healthcheck",
                        "description": "Check whether the WeSpeaker wrapper has loaded the model.",
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
            if tool_name == "speaker_verify":
                result = self._wrapper_instance().predict(arguments)
                text = contract_result_to_json(result)
            elif tool_name == "healthcheck":
                text = json.dumps(self._wrapper_instance().healthcheck())
            else:
                return self._error_response(request_id, -32601, f"Unknown tool: {tool_name}")
            return self._result_response(request_id, text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Tool execution failed")
            return self._error_response(request_id, -32000, str(exc))

    def _result_response(self, request_id: Any, text: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    def _error_response(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
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
        return self._error_response(request.get("id"), -32601, f"Method not found: {method}")

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            response = self.handle_request(json.loads(line))
            if response is not None:
                print(json.dumps(response), flush=True)


if __name__ == "__main__":
    MCPServer().run()
