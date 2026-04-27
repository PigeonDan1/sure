from __future__ import annotations

import contextlib
import io
import json
import math
import os
import time
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MODEL_ROOT.parents[3]
CHECKPOINTS_ROOT = MODEL_ROOT / "checkpoints"
ENROLL = REPO_ROOT / "tests/fixtures/shared/speaker_verification/spk1_enroll.wav"
TRIAL = REPO_ROOT / "tests/fixtures/shared/speaker_verification/spk1_trial.wav"
IMPOSTOR = REPO_ROOT / "tests/fixtures/shared/speaker_verification/spk2_trial.wav"


def main() -> None:
    os.environ["WESPEAKER_HOME"] = str(CHECKPOINTS_ROOT)
    started = time.time()
    result: dict[str, object] = {
        "cache_dir": str(CHECKPOINTS_ROOT),
        "enroll_audio": str(ENROLL),
        "trial_audio": str(TRIAL),
        "impostor_audio": str(IMPOSTOR),
        "tests": {},
    }

    import_started = time.time()
    import wespeaker

    result["tests"]["import"] = {
        "passed": True,
        "duration_ms": round((time.time() - import_started) * 1000, 3),
    }

    load_started = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        model = wespeaker.load_model("english")
    model.set_device("cpu")
    result["tests"]["load"] = {
        "passed": True,
        "duration_ms": round((time.time() - load_started) * 1000, 3),
    }

    infer_started = time.time()
    score = float(model.compute_similarity(str(ENROLL), str(TRIAL)))
    negative_score = float(model.compute_similarity(str(ENROLL), str(IMPOSTOR)))
    result["tests"]["infer"] = {
        "passed": True,
        "duration_ms": round((time.time() - infer_started) * 1000, 3),
    }

    contract_started = time.time()
    payload = {
        "model_name": "wespeaker",
        "task": "speaker_verification",
        "enroll_audio": str(ENROLL),
        "trial_audio": str(TRIAL),
        "score": score,
        "score_is_finite": math.isfinite(score),
        "device": "cpu",
        "error_code": None,
        "backend": "pip",
        "weight_id": "Wespeaker/wespeaker-voxceleb-resnet221-LM",
        "negative_score": negative_score,
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
