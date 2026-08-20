#!/usr/bin/env python3
"""Fail-closed compatibility stub for the retired host MCP smoke command.

Formal SURE-EVAL smoke runs are owned by ``run_smoke.py`` and execute inside
the approved digest-pinned container. Keeping this filename as a stub gives
older callers a deterministic migration error instead of silently launching a
model-local interpreter.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _write(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Retired host MCP smoke command")
    parser.add_argument("--model")
    parser.add_argument("--model-dir")
    parser.add_argument("--output")
    args, _ = parser.parse_known_args()
    payload = {
        "schema": "sure.eval.retired_host_smoke.v1",
        "status": "blocked",
        "ok": False,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "model_dir": args.model_dir,
        "error": {
            "code": "HOST_MODEL_SMOKE_RETIRED",
            "message": "Host/model-local Python smoke is disabled. Run scripts/run_smoke.py from the SURE-EVAL smoke_test unit.",
        },
    }
    _write(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
