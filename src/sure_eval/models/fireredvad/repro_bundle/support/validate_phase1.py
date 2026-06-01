from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MODEL_ROOT.parents[3]
ARTIFACTS_DIR = MODEL_ROOT / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "shared" / "vad" / "en_16k_10s.wav"
WEIGHTS_DIR = MODEL_ROOT / "pretrained_models" / "FireRedVAD" / "VAD"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(stage: str, status: str, message: str, extra: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "timestamp": now_iso(),
        "stage": stage,
        "status": status,
        "message": message,
    }
    if extra:
        payload.update(extra)
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def main() -> None:
    started = time.time()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    if not FIXTURE.exists():
        raise FileNotFoundError(f"Fixture missing: {FIXTURE}")
    if not WEIGHTS_DIR.exists():
        raise FileNotFoundError(f"Weights directory missing: {WEIGHTS_DIR}")

    result: dict[str, Any] = {
        "timestamp": now_iso(),
        "model_id": "FireRedTeam/FireRedVAD",
        "fixture_path": str(FIXTURE),
        "weights_dir": str(WEIGHTS_DIR),
        "tests": {},
    }

    import_started = time.time()
    from fireredvad import FireRedVad, FireRedVadConfig

    result["tests"]["import"] = {
        "passed": True,
        "duration_ms": round((time.time() - import_started) * 1000, 3),
    }
    append_log("VALIDATE_IMPORT", "passed", "Import test passed.")

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
    model = FireRedVad.from_pretrained(str(WEIGHTS_DIR), cfg)
    result["tests"]["load"] = {
        "passed": True,
        "duration_ms": round((time.time() - load_started) * 1000, 3),
    }
    append_log("VALIDATE_LOAD", "passed", "Load test passed.", {"weights_dir": str(WEIGHTS_DIR)})

    infer_started = time.time()
    raw_result, probs = model.detect(str(FIXTURE))
    result["tests"]["infer"] = {
        "passed": True,
        "duration_ms": round((time.time() - infer_started) * 1000, 3),
        "probs_length": len(probs) if hasattr(probs, "__len__") else None,
    }
    append_log(
        "VALIDATE_INFER",
        "passed",
        "Infer test passed.",
        {"fixture_source": "shared", "fixture_used": str(FIXTURE)},
    )

    contract_started = time.time()
    if not isinstance(raw_result, dict):
        raise AssertionError("Expected raw result to be a dict")
    for key in ("dur", "timestamps", "wav_path"):
        if key not in raw_result:
            raise AssertionError(f"Missing required field: {key}")
    timestamps = raw_result["timestamps"]
    if not isinstance(timestamps, list) or not timestamps:
        raise AssertionError("timestamps must be a non-empty list")
    json.dumps(raw_result, ensure_ascii=False)
    SAMPLE_OUTPUT.write_text(json.dumps(raw_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["tests"]["contract"] = {
        "passed": True,
        "duration_ms": round((time.time() - contract_started) * 1000, 3),
    }
    append_log(
        "VALIDATE_CONTRACT",
        "passed",
        "Contract test passed.",
        {"primary_field": "timestamps", "required_fields": ["dur", "timestamps", "wav_path"]},
    )

    result["result"] = raw_result
    result["overall"] = "PASSED"
    result["duration_seconds"] = round(time.time() - started, 3)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
