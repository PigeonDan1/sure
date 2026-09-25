"""Stdio MCP entrypoint for the MetricGAN+ SURE example."""

from __future__ import annotations

import json
import sys
import traceback
from contextlib import redirect_stdout

from model import ModelWrapper


def respond(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    model = ModelWrapper()
    for line in sys.stdin:
        if not line.strip():
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            if request_id is None and method == "notifications/initialized":
                continue
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "metricgan-plus-voicebank", "version": "1"},
                    "capabilities": {"tools": {}},
                }
            elif method == "tools/list":
                result = {
                    "tools": [{
                        "name": "enhance_speech",
                        "description": "Enhance noisy speech and return a WAV path",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "audio_path": {"type": "string"},
                                "noisy_audio_path": {"type": "string"},
                                "output_path": {"type": "string"},
                            },
                            "required": ["audio_path"],
                        },
                    }]
                }
            elif method == "tools/call":
                params = request.get("params") or {}
                if params.get("name") != "enhance_speech":
                    raise ValueError("unknown MCP tool")
                with redirect_stdout(sys.stderr):
                    output = model.predict(params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(output)}]}
            elif method == "shutdown":
                respond({"jsonrpc": "2.0", "id": request_id, "result": {}})
                return 0
            else:
                raise ValueError(f"unsupported MCP method: {method}")
            respond({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:
            print(traceback.format_exc(), file=sys.stderr)
            respond({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
