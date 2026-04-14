from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    upstream_root = Path(os.environ["SURE_REPRO_UPSTREAM_ROOT"]).resolve()
    weights_dir = Path(os.environ["SURE_REPRO_WEIGHTS_DIR"]).resolve()
    fixture_path = Path(os.environ["SURE_REPRO_FIXTURE"]).resolve()
    output_path = Path(os.environ["SURE_REPRO_OUTPUT"]).resolve()

    if not upstream_root.exists():
        raise FileNotFoundError(f"Missing upstream snapshot: {upstream_root}")
    if not weights_dir.exists():
        raise FileNotFoundError(f"Missing weights directory: {weights_dir}")
    if not fixture_path.exists():
        raise FileNotFoundError(f"Missing fixture: {fixture_path}")

    sys.path.insert(0, str(upstream_root))

    started = time.time()
    result: dict[str, object] = {
        "timestamp": now_iso(),
        "upstream_root": str(upstream_root),
        "weights_dir": str(weights_dir),
        "fixture_path": str(fixture_path),
        "tests": {},
    }

    import_started = time.time()
    from fireredasr2s.fireredasr2 import FireRedAsr2, FireRedAsr2Config

    result["tests"]["import"] = {
        "passed": True,
        "duration_ms": round((time.time() - import_started) * 1000, 3),
    }

    load_started = time.time()
    config = FireRedAsr2Config(
        use_gpu=False,
        use_half=False,
        beam_size=3,
        nbest=1,
        decode_max_len=0,
        softmax_smoothing=1.25,
        aed_length_penalty=0.6,
        eos_penalty=1.0,
        return_timestamp=False,
    )
    model = FireRedAsr2.from_pretrained("aed", str(weights_dir), config)
    result["tests"]["load"] = {
        "passed": True,
        "duration_ms": round((time.time() - load_started) * 1000, 3),
    }

    infer_started = time.time()
    outputs = model.transcribe(["sure_en_16k_10s"], [str(fixture_path)])
    result["tests"]["infer"] = {
        "passed": True,
        "duration_ms": round((time.time() - infer_started) * 1000, 3),
    }

    contract_started = time.time()
    if not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], dict):
        raise AssertionError("Expected a single dict result")
    sample = outputs[0]
    for key in ("uttid", "text"):
        if key not in sample:
            raise AssertionError(f"Missing required field: {key}")
    if not isinstance(sample["text"], str) or not sample["text"].strip():
        raise AssertionError("Field `text` must be non-empty")
    json.dumps(sample, ensure_ascii=False)
    result["tests"]["contract"] = {
        "passed": True,
        "duration_ms": round((time.time() - contract_started) * 1000, 3),
    }

    result["result"] = sample
    result["overall"] = "PASSED"
    result["duration_seconds"] = round(time.time() - started, 3)

    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
