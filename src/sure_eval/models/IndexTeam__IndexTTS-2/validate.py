#!/usr/bin/env python3
"""Local validation for IndexTeam/IndexTTS-2."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf


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
        "artifacts/xforge_sure_handoff.json",
        "artifacts/weights_manifest.json",
    ]
    missing = [item for item in required if not (MODEL_DIR / item).exists()]
    if missing:
        _log("VALIDATE_SPEC", "failed", "Missing required files: " + ", ".join(missing))
        return 2
    if not list((MODEL_DIR / "fixture/zh").glob("*.mp3")):
        _log("VALIDATE_SPEC", "failed", "Missing fixture mp3 under fixture/zh.")
        return 2
    _log("VALIDATE_SPEC", "passed", "Static onboarding files are present.")
    return 0


def main() -> int:
    os.environ.setdefault("MPLCONFIGDIR", str(MODEL_DIR / ".runtime/matplotlib"))
    os.environ.setdefault("HF_HOME", str(MODEL_DIR / ".runtime/huggingface"))
    os.environ.setdefault("HF_HUB_CACHE", str(MODEL_DIR / ".runtime/huggingface/hub"))

    static_status = validate_static()
    if static_status != 0:
        return static_status
    if os.environ.get("SURE_XFORGE_STATIC_ONLY", "0") == "1":
        return 0

    sys.path.insert(0, str(MODEL_DIR))
    from model import ModelWrapper

    prompt_audio = MODEL_DIR / "fixture/zh/ZH_B00001_S00000_W000000.mp3"
    if not prompt_audio.exists():
        prompt_audio = sorted((MODEL_DIR / "fixture/zh").glob("*.mp3"))[0]
    target_text = (
        "这是一个新的本地语音合成验证样例，用来确认模型会根据输入文本重新生成语音，"
        "而不是返回参考音频本身。"
    )
    output_path = MODEL_DIR / "artifacts/outputs/indextts2_emilia_smoke.wav"

    wrapper = ModelWrapper({"device": os.environ.get("DEVICE", "cpu")})
    _log("VALIDATE_IMPORT", "passed", "ModelWrapper imported.", health=wrapper.healthcheck())
    wrapper.load()
    _log("VALIDATE_LOAD", "passed", "IndexTTS-2 model loaded.", health=wrapper.healthcheck())

    result = wrapper.predict(
        {
            "prompt_audio_path": str(prompt_audio),
            "text": target_text,
            "output_path": str(output_path),
            "language": "zh",
        }
    )
    output = Path(result.audio_path)
    if not output.exists() or output.stat().st_size <= 0:
        _log("VALIDATE_INFER", "failed", f"Missing or empty output audio: {output}")
        return 4
    data, sample_rate = sf.read(output)
    if len(data) == 0:
        _log("VALIDATE_CONTRACT", "failed", "Output audio has zero samples.", audio_path=str(output))
        return 5
    _log(
        "VALIDATE_CONTRACT",
        "passed",
        "TTS output satisfies SURE audio_path contract.",
        audio_path=str(output),
        sample_rate=int(sample_rate),
        num_samples=int(len(data)),
        result=result.to_dict(),
        prompt_audio_path=str(prompt_audio),
        target_text=target_text,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
