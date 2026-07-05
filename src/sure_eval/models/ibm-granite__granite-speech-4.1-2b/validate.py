#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = MODEL_DIR / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
FIXTURE_GT = MODEL_DIR / "fixture" / "asr" / "asr_en" / "gt.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(stage: str, status: str, message: str, **extra: Any) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": now_iso(),
        "stage": stage,
        "status": status,
        "message": message,
        **extra,
    }
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_fixture() -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with FIXTURE_GT.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            audio_path = FIXTURE_GT.parent / item["audio"]
            if not audio_path.exists():
                raise FileNotFoundError(f"Missing fixture audio: {audio_path}")
            item["_audio_path"] = str(audio_path)
            samples.append(item)
    if not samples:
        raise ValueError(f"No samples in {FIXTURE_GT}")
    return samples


def validate_contract(result: dict[str, Any]) -> None:
    json.dumps(result, ensure_ascii=False)
    if "text" not in result:
        raise AssertionError("Missing required field: text")
    if not isinstance(result["text"], str):
        raise AssertionError("Field text must be a string")
    if not result["text"].strip():
        raise AssertionError("Field text must be non-empty")


def main() -> int:
    started = time.time()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    if str(MODEL_DIR) not in sys.path:
        sys.path.insert(0, str(MODEL_DIR))

    results: dict[str, Any] = {
        "timestamp": now_iso(),
        "model_id": "ibm-granite/granite-speech-4.1-2b",
        "revision": "de575db64086f84fdc79da4932d1076e965bc546",
        "task": "ASR",
        "overall": "FAILED",
        "samples": [],
        "tests": {},
    }

    try:
        samples = load_fixture()
        import_start = time.time()
        import torch
        import torchaudio
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
        from model import ModelWrapper

        del snapshot_download, AutoModelForSpeechSeq2Seq, AutoProcessor
        results["tests"]["import"] = {"passed": True, "duration_ms": round((time.time() - import_start) * 1000, 3)}
        log(
            "VALIDATE_IMPORT",
            "passed",
            "Imported torch, torchaudio, huggingface_hub, transformers, and ModelWrapper.",
            torch_version=torch.__version__,
            torchaudio_version=torchaudio.__version__,
            cuda_available=torch.cuda.is_available(),
            device_env=os.environ.get("DEVICE", "auto"),
        )

        load_start = time.time()
        wrapper = ModelWrapper()
        wrapper.load()
        results["tests"]["load"] = {"passed": True, "duration_ms": round((time.time() - load_start) * 1000, 3)}
        log("VALIDATE_LOAD", "passed", "ModelWrapper.load() completed.", health=wrapper.healthcheck())

        infer_start = time.time()
        for sample in samples:
            prediction = wrapper.predict({"audio_path": sample["_audio_path"]})
            log("VALIDATE_INFER", "passed", "Inference completed.", key=sample["key"], prediction=prediction)
            validate_contract(prediction)
            log("VALIDATE_CONTRACT", "passed", "Output satisfies ASR JSON text contract.", key=sample["key"], prediction=prediction)
            results["samples"].append(
                {
                    "key": sample["key"],
                    "audio": sample["audio"],
                    "ground_truth": sample.get("ground_truth"),
                    "prediction": prediction["text"],
                    "result": prediction,
                }
            )
        results["tests"]["infer"] = {"passed": True, "duration_ms": round((time.time() - infer_start) * 1000, 3)}
        results["tests"]["contract"] = {"passed": True}
        results["overall"] = "PASSED"
        return 0
    except Exception as exc:  # noqa: BLE001 - validation log boundary
        results["error"] = repr(exc)
        log("VALIDATE_RUN", "failed", str(exc), error_type=type(exc).__name__)
        return 1
    finally:
        results["duration_seconds"] = round(time.time() - started, 3)
        SAMPLE_OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
