#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


MODEL_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = MODEL_DIR / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"


def _log(stage: str, status: str, message: str, **extra: object) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "status": status,
        "message": message,
        **extra,
    }
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def validate_static() -> int:
    required = [
        "model.spec.yaml",
        "config.yaml",
        "model.py",
        "server.py",
        "__init__.py",
        "artifacts/backend_choice.json",
        "artifacts/build_plan.json",
        "artifacts/weights_manifest.json",
        ".runtime/huggingface/GilgameshWind/X-ASR-zh-en/deployment/models/chunk-960ms-model/encoder-960ms.onnx",
        ".runtime/huggingface/GilgameshWind/X-ASR-zh-en/deployment/models/chunk-960ms-model/decoder-960ms.onnx",
        ".runtime/huggingface/GilgameshWind/X-ASR-zh-en/deployment/models/chunk-960ms-model/joiner-960ms.onnx",
        ".runtime/huggingface/GilgameshWind/X-ASR-zh-en/deployment/models/chunk-960ms-model/tokens.txt",
    ]
    missing = [item for item in required if not (MODEL_DIR / item).exists()]
    fixture_files = sorted((MODEL_DIR / "fixture/asr/asr_en").glob("*.wav"))
    if not fixture_files:
        missing.append("fixture/asr/asr_en/*.wav")
    if missing:
        _log("VALIDATE_SPEC", "failed", "Missing required files: " + ", ".join(missing))
        return 2
    _log("VALIDATE_SPEC", "passed", "Static onboarding files are present.")
    return 0


def main() -> int:
    static_status = validate_static()
    if static_status != 0:
        return static_status
    if os.environ.get("SURE_XFORGE_STATIC_ONLY", "0") == "1":
        return 0

    sys.path.insert(0, str(MODEL_DIR))
    from model import ModelWrapper

    fixture_sets = {
        "asr_en": sorted((MODEL_DIR / "fixture/asr/asr_en").glob("*.wav")),
        "asr_zh": sorted((MODEL_DIR / "fixture/asr/asr_zh").glob("*.wav")),
    }
    missing_fixtures = [name for name, paths in fixture_sets.items() if not paths]
    if missing_fixtures:
        _log("VALIDATE_SPEC", "failed", "Missing fixture sets: " + ", ".join(missing_fixtures))
        return 3
    wrapper = ModelWrapper({"provider": os.environ.get("SHERPA_ONNX_PROVIDER", "cpu")})
    _log("VALIDATE_IMPORT", "passed", "ModelWrapper imported.", health=wrapper.healthcheck())
    wrapper.load()
    _log("VALIDATE_LOAD", "passed", "X-ASR sherpa-onnx model loaded.", health=wrapper.healthcheck())
    outputs = {}
    for subtask, paths in fixture_sets.items():
        result = wrapper.predict(str(paths[0]))
        if not isinstance(result.text, str):
            _log("VALIDATE_CONTRACT", "failed", "Result text is not a string.", subtask=subtask, result=result.to_dict())
            return 4
        outputs[subtask] = result.to_dict()
        _log("VALIDATE_INFER", "passed", "X-ASR inference completed.", subtask=subtask, result=result.to_dict())
        _log("VALIDATE_CONTRACT", "passed", "ASR output satisfies SURE text contract.", subtask=subtask, result=result.to_dict())
    (ARTIFACTS_DIR / "sample_output.json").write_text(
        json.dumps(outputs, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
