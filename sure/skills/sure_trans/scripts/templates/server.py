#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import traceback
from contextlib import redirect_stdout

from model import ModelWrapper


TOOL_NAME = "__TOOL_NAME__"
INPUT_SCHEMA = __INPUT_SCHEMA__


def respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    wrapper = ModelWrapper()
    for line in sys.stdin:
        if not line.strip():
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            if method == "initialize":
                respond({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "sure-trans-adapter", "version": "1"}, "capabilities": {"tools": {}}}})
            elif method == "tools/list":
                respond({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": TOOL_NAME, "description": "Run transformed model inference", "inputSchema": INPUT_SCHEMA}]}})
            elif method == "tools/call":
                params = request.get("params") if isinstance(request.get("params"), dict) else {}
                if params.get("name") not in {TOOL_NAME, "predict", "healthcheck"}:
                    raise ValueError(f"unknown tool: {params.get('name')}")
                with redirect_stdout(sys.stderr):
                    if params.get("name") == "healthcheck":
                        result = wrapper.healthcheck()
                    else:
                        if wrapper.model is None:
                            wrapper.load()
                        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                        result = wrapper.predict(arguments)
                respond({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}})
            elif method == "shutdown":
                respond({"jsonrpc": "2.0", "id": request_id, "result": {}})
                return 0
            else:
                raise ValueError(f"unsupported method: {method}")
        except Exception as error:
            print(traceback.format_exc(), file=sys.stderr)
            respond({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(error)}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
