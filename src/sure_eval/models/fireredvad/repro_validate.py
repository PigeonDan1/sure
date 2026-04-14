from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    fixture = Path(os.environ["SURE_REPRO_FIXTURE"]).resolve()
    weights_dir = Path(os.environ["SURE_REPRO_WEIGHTS_DIR"]).resolve()
    output_path = Path(os.environ["SURE_REPRO_OUTPUT"]).resolve()

    if not fixture.exists():
        raise FileNotFoundError(f"Fixture missing: {fixture}")
    if not weights_dir.exists():
        raise FileNotFoundError(f"Weights directory missing: {weights_dir}")

    started = time.time()

    import_started = time.time()
    from fireredvad import FireRedVad, FireRedVadConfig

    import_duration_ms = round((time.time() - import_started) * 1000, 3)

    load_started = time.time()
    cfg = FireRedVadConfig(
        use_gpu=False,
        smooth_window_size=5,
        speech_threshold=0.4,
        min_speech_frame=20,
        max_speech_frame=2000,
        min_silence_frame=20,
        merge_silence_frame=0,
        extend_speech_frame=0,
        chunk_max_frame=30000,
    )
    model = FireRedVad.from_pretrained(str(weights_dir), cfg)
    load_duration_ms = round((time.time() - load_started) * 1000, 3)

    infer_started = time.time()
    raw_result, probs = model.detect(str(fixture))
    infer_duration_ms = round((time.time() - infer_started) * 1000, 3)

    if not isinstance(raw_result, dict):
        raise AssertionError("Expected dict output")
    for key in ("dur", "timestamps", "wav_path"):
        if key not in raw_result:
            raise AssertionError(f"Missing required field: {key}")
    if not isinstance(raw_result["timestamps"], list) or not raw_result["timestamps"]:
        raise AssertionError("timestamps must be a non-empty list")
    json.dumps(raw_result, ensure_ascii=False)

    payload = {
        "timestamp": now_iso(),
        "fixture_path": str(fixture),
        "weights_dir": str(weights_dir),
        "tests": {
            "import": {"passed": True, "duration_ms": import_duration_ms},
            "load": {"passed": True, "duration_ms": load_duration_ms},
            "infer": {
                "passed": True,
                "duration_ms": infer_duration_ms,
                "probs_length": len(probs) if hasattr(probs, "__len__") else None,
            },
            "contract": {"passed": True, "duration_ms": 0.0},
        },
        "result": raw_result,
        "overall": "PASSED",
        "duration_seconds": round(time.time() - started, 3),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
