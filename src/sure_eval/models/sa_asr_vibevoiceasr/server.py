"""MCP server for VibeVoice-ASR."""

import json
import sys
from pathlib import Path

# Ensure model-local venv packages are importable
sys.path.insert(0, str(Path(__file__).parent / ".venv" / "lib64" / "python3.11" / "site-packages"))
sys.path.insert(0, str(Path(__file__).parent / ".venv" / "lib" / "python3.11" / "site-packages"))

from model import ModelWrapper


def main():
    wrapper = ModelWrapper()
    wrapper.load()

    for line in sys.stdin:
        req = json.loads(line)
        method = req.get("method")
        params = req.get("params", {})
        req_id = req.get("id")

        if method == "transcribe":
            audio_path = params.get("audio_path", "")
            max_new_tokens = params.get("max_new_tokens", 256)
            try:
                text = wrapper.transcribe(audio_path, max_new_tokens=max_new_tokens)
                resp = {"jsonrpc": "2.0", "id": req_id, "result": {"text": text}}
            except Exception as e:
                resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}
        else:
            resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}

        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
