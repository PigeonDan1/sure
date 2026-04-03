import json
import time
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MODEL_ROOT.parents[3]
FIXTURE = REPO_ROOT / "tests/fixtures/shared/vad/en_16k_10s.wav"
MODEL_DIR = MODEL_ROOT / "pretrained_models/FireRedVAD/VAD"


def main() -> None:
    started = time.time()
    result = {
        "fixture_path": str(FIXTURE),
        "model_dir": str(MODEL_DIR),
        "tests": {}
    }

    import_started = time.time()
    from fireredvad import FireRedVad, FireRedVadConfig

    result["tests"]["import"] = {
        "passed": True,
        "duration_ms": round((time.time() - import_started) * 1000, 3)
    }

    load_started = time.time()
    vad_config = FireRedVadConfig(
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
    vad = FireRedVad.from_pretrained(str(MODEL_DIR), vad_config)
    result["tests"]["load"] = {
        "passed": True,
        "duration_ms": round((time.time() - load_started) * 1000, 3)
    }

    infer_started = time.time()
    infer_result, probs = vad.detect(str(FIXTURE))
    result["tests"]["infer"] = {
        "passed": True,
        "duration_ms": round((time.time() - infer_started) * 1000, 3),
        "probs_shape": list(probs.shape),
    }

    contract_started = time.time()
    json_ready = {
        "dur": infer_result.get("dur"),
        "timestamps": [list(pair) for pair in infer_result.get("timestamps", [])],
        "wav_path": infer_result.get("wav_path"),
    }
    json.dumps(json_ready)
    if not json_ready["timestamps"]:
        raise AssertionError("timestamps must be non-empty")
    result["tests"]["contract"] = {
        "passed": True,
        "duration_ms": round((time.time() - contract_started) * 1000, 3),
    }

    result["result"] = json_ready
    result["overall"] = "PASSED"
    result["duration_seconds"] = round(time.time() - started, 3)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
