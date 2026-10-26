"""Default JSONL server for model wrappers with load() and predict(payload)."""

from __future__ import annotations

import json
import sys

from model import ModelWrapper


def main() -> int:
    wrapper = ModelWrapper()
    wrapper.load()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("JSONL request must be an object")
            prediction = wrapper.predict(request)
            if not isinstance(prediction, dict):
                raise ValueError("ModelWrapper.predict must return a prediction object")
            sys.stdout.write(json.dumps(prediction, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except (TypeError, ValueError, OSError) as exc:
            print(f"JSONL server failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
