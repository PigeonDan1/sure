from __future__ import annotations

import json
import sys
from typing import Any


class MCPServer:
    def __init__(self) -> None:
        self._wrapper = None

    def _get_wrapper(self):
        if self._wrapper is None:
            from model import ModelWrapper

            self._wrapper = ModelWrapper()
        return self._wrapper

    def handle_initialize(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "serverInfo": {
                    "name": "qwen2_vl_2b_instruct",
                    "version": "0.1.0",
                }
            },
        }

    def handle_tools_list(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "describe_image",
                        "description": "Run a minimal single-image prompt through Qwen2-VL-2B-Instruct.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "image_path": {"type": "string"},
                                "prompt": {"type": "string"},
                                "max_new_tokens": {"type": "integer"},
                            },
                            "required": ["image_path"],
                        },
                    }
                ]
            },
        }

    def handle_tools_call(self, request: dict[str, Any]) -> dict[str, Any]:
        params = request.get("params", {})
        arguments = params.get("arguments", {})
        result = self._get_wrapper().predict(arguments)
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "content": [
                    {
                        "type": "json",
                        "json": result,
                    }
                ]
            },
        }

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
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
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        }

    def serve_forever(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = self.dispatch(request)
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32000, "message": str(exc)},
                        }
                    ),
                    flush=True,
                )
                print(f"server error: {exc}", file=sys.stderr, flush=True)
                continue
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    MCPServer().serve_forever()
