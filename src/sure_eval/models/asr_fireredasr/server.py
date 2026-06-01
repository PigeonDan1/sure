#!/usr/bin/env python3
"""
FireRedASR-LLM-L MCP Server for SURE-EVAL.

MCP server implementation for speech recognition using FireRedTeam/FireRedASR-LLM-L.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


class FireRedAsrServer:
    """MCP Server for FireRedASR-LLM-L."""

    def __init__(self):
        self._initialized = False
        self._model = None
        self._model_path = os.environ.get(
            "FIREREDASR_MODEL_PATH",
            str(Path(__file__).parent / "checkpoints" / "pretrained_models" / "fireredasr_llm_l"),
        )
        self._device = os.environ.get("DEVICE", "auto")
        # Protocol params (backward-compatible: no-op if not set)
        self._protocol_id = os.environ.get("SURE_EVAL_PROTOCOL_ID", "")
        self._protocol_params = {}
        for key, value in os.environ.items():
            if key.startswith("SURE_EVAL_PROTOCOL_") or key.startswith("SURE_EVAL_MODEL_"):
                self._protocol_params[key] = value

        # Tool definitions
        self._tools = [
            {
                "name": "asr_transcribe",
                "description": "Transcribe speech to text using FireRedASR-LLM-L",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "audio_path": {
                            "type": "string",
                            "description": "Path to audio file"
                        },
                        "language": {
                            "type": "string",
                            "description": "Language hint (optional)",
                            "default": "auto"
                        }
                    },
                    "required": ["audio_path"]
                }
            }
        ]

    def _load_model(self) -> None:
        """Lazy load model."""
        if self._model is not None:
            return

        try:
            from model import ModelWrapper
        except ImportError:
            sys.path.insert(0, str(Path(__file__).parent))
            from model import ModelWrapper

        print(f"Loading FireRedASR-LLM-L model: {self._model_path}", file=sys.stderr)
        self._model = ModelWrapper(
            model_path=self._model_path,
            device=self._device,
        )
        print("Model loaded", file=sys.stderr)

    def run(self) -> None:
        """Run the server."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
                response = self._handle_request(request)
                if response:
                    self._send_response(response)
            except json.JSONDecodeError as e:
                self._send_error(None, -32700, f"Parse error: {e}")
            except Exception as e:
                request_id = request.get("id") if isinstance(request, dict) else None
                self._send_error(request_id, -32603, f"Internal error: {e}")

    def _handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle JSON-RPC request."""
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return self._handle_initialize(request_id, params)
        elif method == "notifications/initialized":
            return None
        elif method == "tools/list":
            return self._handle_tools_list(request_id)
        elif method == "tools/call":
            return self._handle_tools_call(request_id, params)
        elif method == "shutdown":
            return self._handle_shutdown(request_id)
        else:
            return self._error_response(request_id, -32601, f"Method not found: {method}")

    def _handle_initialize(self, request_id: Any, params: dict) -> dict[str, Any]:
        """Handle initialize request."""
        self._initialized = True
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "asr-fireredasr-server",
                    "version": "1.0.0"
                }
            }
        }

    def _handle_tools_list(self, request_id: Any) -> dict[str, Any]:
        """Handle tools/list request."""
        if not self._initialized:
            return self._error_response(request_id, -32001, "Server not initialized")

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": self._tools}
        }

    def _handle_tools_call(self, request_id: Any, params: dict) -> dict[str, Any]:
        """Handle tools/call request."""
        if not self._initialized:
            return self._error_response(request_id, -32001, "Server not initialized")

        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        try:
            result = self._execute_tool(tool_name, arguments)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as e:
            error_msg = str(e)
            print(f"Tool execution error: {error_msg}", file=sys.stderr)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {error_msg}"}],
                    "isError": True,
                    "error": error_msg
                }
            }

    def _execute_tool(self, tool_name: str, arguments: dict) -> dict[str, Any]:
        """Execute a tool."""
        self._load_model()

        if tool_name == "asr_transcribe":
            return self._transcribe(arguments)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    def _transcribe(self, arguments: dict) -> dict[str, Any]:
        """Transcribe audio."""
        audio_path = arguments.get("audio_path")
        language = arguments.get("language", "auto")

        if not audio_path:
            raise ValueError("audio_path is required")

        if self._protocol_id:
            print(
                f"[Protocol {self._protocol_id}] Transcribing: {audio_path} "
                f"(language={language}, params={self._protocol_params})",
                file=sys.stderr,
            )
        else:
            print(f"Transcribing: {audio_path} (language={language})", file=sys.stderr)

        result = self._model.transcribe(
            audio_path,
            language=language if language != "auto" else None,
        )

        return {
            "content": [{"type": "text", "text": result.text}],
            "isError": False
        }

    def _handle_shutdown(self, request_id: Any) -> dict[str, Any]:
        """Handle shutdown request."""
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    def _error_response(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        """Create error response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message}
        }

    def _send_response(self, response: dict[str, Any]) -> None:
        """Send response to stdout."""
        print(json.dumps(response), flush=True)

    def _send_error(self, request_id: Any, code: int, message: str) -> None:
        """Send error response."""
        self._send_response(self._error_response(request_id, code, message))


if __name__ == "__main__":
    server = FireRedAsrServer()
    server.run()
