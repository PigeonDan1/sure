"""Minimal callable server shim for the SURE tool-agent contract."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import torch

from model import DEFAULT_KEYWORDS, WeKWSModel


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: .venv/bin/python server.py /path/to/audio.wav", file=sys.stderr)
        return 2

    model = WeKWSModel(
        keywords=os.environ.get("WEKWS_KEYWORDS", DEFAULT_KEYWORDS),
        threshold=float(os.environ.get("WEKWS_THRESHOLD", "0.0")),
        gpu=int(os.environ.get("WEKWS_GPU", "0" if torch.cuda.is_available() else "-1")),
    )
    result = model.predict(Path(sys.argv[1]))
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
