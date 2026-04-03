from __future__ import annotations

import json
import os
import time
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MODEL_ROOT.parents[3]
ENROLL = REPO_ROOT / "tests/fixtures/shared/speaker_verification/spk1_enroll.wav"
TRIAL = REPO_ROOT / "tests/fixtures/shared/speaker_verification/spk1_trial.wav"
NEGATIVE = REPO_ROOT / "tests/fixtures/shared/speaker_verification/spk2_trial.wav"
CACHE_DIR = MODEL_ROOT / "pretrained_models" / "wespeaker"


def main() -> None:
    os.environ["WESPEAKER_HOME"] = str(CACHE_DIR)
    started = time.time()
    result: dict[str, object] = {
        "cache_dir": str(CACHE_DIR),
        "enrollment_audio": str(ENROLL),
        "trial_audio": str(TRIAL),
        "optional_negative_audio": str(NEGATIVE),
        "tests": {},
    }

    import_started = time.time()
    import wespeaker

    result["tests"]["import"] = {
        "passed": True,
        "duration_ms": round((time.time() - import_started) * 1000, 3),
    }

    load_started = time.time()
    model = wespeaker.load_model("english")
    model.set_device("cpu")
    result["tests"]["load"] = {
        "passed": True,
        "duration_ms": round((time.time() - load_started) * 1000, 3),
    }

    infer_started = time.time()
    score = model.compute_similarity(str(ENROLL), str(TRIAL))
    result["tests"]["infer"] = {
        "passed": True,
        "duration_ms": round((time.time() - infer_started) * 1000, 3),
    }

    negative_score = model.compute_similarity(str(ENROLL), str(NEGATIVE))

    contract_started = time.time()
    if not isinstance(score, (int, float)):
        raise AssertionError("similarity_score must be numeric")
    payload = {
        "similarity_score": float(score),
        "negative_similarity_score": float(negative_score),
        "enrollment_audio": str(ENROLL),
        "trial_audio": str(TRIAL),
        "model_name": "english",
        "device": "cpu",
        "cache_dir": str(CACHE_DIR),
    }
    json.dumps(payload)
    result["tests"]["contract"] = {
        "passed": True,
        "duration_ms": round((time.time() - contract_started) * 1000, 3),
    }

    result["result"] = payload
    result["overall"] = "PASSED"
    result["duration_seconds"] = round(time.time() - started, 3)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
