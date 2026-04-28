#!/usr/bin/env python3

import json
import math
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: output_contract_check.py <output.json>", file=sys.stderr)
        return 2

    output_path = Path(sys.argv[1]).resolve()
    payload = json.loads(output_path.read_text())

    required_fields = [
        "segments",
        "sample_rate",
        "audio_path",
        "audio_duration_sec",
        "model_backend",
        "error_code",
    ]
    missing = [field for field in required_fields if field not in payload]
    if missing:
        raise SystemExit(f"missing required fields: {missing}")

    if payload["sample_rate"] != 16000:
        raise SystemExit("sample_rate must equal 16000")
    if not payload["model_backend"]:
        raise SystemExit("model_backend must be non-empty")
    if payload["audio_duration_sec"] <= 0:
        raise SystemExit("audio_duration_sec must be positive")
    if not isinstance(payload["segments"], list):
        raise SystemExit("segments must be a list")

    duration = float(payload["audio_duration_sec"])
    tolerance = 1e-6
    for index, segment in enumerate(payload["segments"]):
        if "start" not in segment or "end" not in segment:
            raise SystemExit(f"segment {index} missing start/end")
        start = float(segment["start"])
        end = float(segment["end"])
        if not (0 <= start < end <= duration + tolerance):
            raise SystemExit(f"segment {index} violates boundary rule: {segment}")
        if math.isnan(start) or math.isnan(end):
            raise SystemExit(f"segment {index} contains NaN")

    print(json.dumps({"status": "passed", "output_path": str(output_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
