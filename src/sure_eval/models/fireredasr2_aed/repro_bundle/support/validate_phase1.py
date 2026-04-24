from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MODEL_ROOT.parents[3]
UPSTREAM_ROOT = MODEL_ROOT / "upstream" / "FireRedASR2S"
WEIGHTS_DIR = MODEL_ROOT / "pretrained_models" / "FireRedASR2-AED"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "shared" / "asr" / "en_16k_10s.wav"
ARTIFACTS_DIR = MODEL_ROOT / "artifacts"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"


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

    if not UPSTREAM_ROOT.exists():
        raise FileNotFoundError(f"Upstream repo missing: {UPSTREAM_ROOT}")
    if not WEIGHTS_DIR.exists():
        raise FileNotFoundError(f"Weights directory missing: {WEIGHTS_DIR}")
    if not FIXTURE.exists():
        raise FileNotFoundError(f"Fixture missing: {FIXTURE}")

    os.environ["PYTHONPATH"] = str(UPSTREAM_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")
    sys.path.insert(0, str(UPSTREAM_ROOT))

    result: dict[str, Any] = {
        "timestamp": now_iso(),
        "model_id": "FireRedTeam/FireRedASR2-AED",
        "weights_dir": str(WEIGHTS_DIR),
        "fixture_path": str(FIXTURE),
        "tests": {},
    }

    import_started = time.time()
    from fireredasr2s.fireredasr2 import FireRedAsr2, FireRedAsr2Config

    result["tests"]["import"] = {
        "passed": True,
        "duration_ms": round((time.time() - import_started) * 1000, 3),
    }
    append_log("VALIDATE_IMPORT", "passed", "Import test passed.")

    load_started = time.time()
    asr_config = FireRedAsr2Config(
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
    model = FireRedAsr2.from_pretrained("aed", str(WEIGHTS_DIR), asr_config)
    result["tests"]["load"] = {
        "passed": True,
        "duration_ms": round((time.time() - load_started) * 1000, 3),
    }
    append_log("VALIDATE_LOAD", "passed", "Load test passed.", {"weights_dir": str(WEIGHTS_DIR)})

    infer_started = time.time()
    outputs = model.transcribe(["sure_en_16k_10s"], [str(FIXTURE)])
    result["tests"]["infer"] = {
        "passed": True,
        "duration_ms": round((time.time() - infer_started) * 1000, 3),
    }
    append_log("VALIDATE_INFER", "passed", "Infer test passed.", {"fixture_source": "shared"})

    contract_started = time.time()
    if not isinstance(outputs, list) or len(outputs) != 1:
        raise AssertionError("Expected one result item")
    sample = outputs[0]
    if not isinstance(sample, dict):
        raise AssertionError("Expected dict result")
    for key in ["uttid", "text"]:
        if key not in sample:
            raise AssertionError(f"Missing required field: {key}")
    if not isinstance(sample["text"], str) or not sample["text"].strip():
        raise AssertionError("Transcript must be a non-empty string")
    json.dumps(sample, ensure_ascii=False)
    SAMPLE_OUTPUT.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["tests"]["contract"] = {
        "passed": True,
        "duration_ms": round((time.time() - contract_started) * 1000, 3),
    }
    append_log("VALIDATE_CONTRACT", "passed", "Contract test passed.", {"primary_field": "text"})

    result["result"] = sample
    result["overall"] = "PASSED"
    result["duration_seconds"] = round(time.time() - started, 3)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
