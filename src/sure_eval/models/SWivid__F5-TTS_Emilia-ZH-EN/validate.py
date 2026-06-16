#!/usr/bin/env python3
"""Local validation for SWivid/F5-TTS_Emilia-ZH-EN."""

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
    fixture_files = sorted((MODEL_DIR / "fixture/zh").glob("*.mp3"))
    if not fixture_files:
        _log("VALIDATE_SPEC", "failed", "Missing fixture mp3 under fixture/zh.")
        return 2
    _log("VALIDATE_SPEC", "passed", "Static onboarding files are present.")
    return 0


def _load_prompt_text(prompt_audio: Path) -> str:
    override = os.environ.get("SURE_TTS_PROMPT_TEXT")
    if override:
        return override

    candidates = [
        MODEL_DIR / "fixture/zh/gt.jsonl",
        MODEL_DIR / "fixture/gt.jsonl",
    ]
    candidates.extend(sorted((MODEL_DIR / "fixture").glob("**/*.jsonl")))
    audio_id = prompt_audio.stem
    for jsonl_path in dict.fromkeys(candidates):
        if not jsonl_path.exists():
            continue
        with jsonl_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                key = str(item.get("id") or item.get("key") or "")
                path = str(item.get("wav") or item.get("path") or item.get("audio") or "")
                if key == audio_id or Path(path).stem == audio_id:
                    text = str(item.get("text") or item.get("prompt_text") or "")
                    if text:
                        return text
    raise FileNotFoundError(
        f"Missing prompt text for {prompt_audio.name}. Add a jsonl record under "
        "fixture/zh/gt.jsonl with id/key or wav/path matching the audio stem, "
        "or set SURE_TTS_PROMPT_TEXT. F5-TTS will otherwise try online ASR."
    )


def main() -> int:
    os.environ.setdefault("MPLCONFIGDIR", str(MODEL_DIR / ".runtime/matplotlib"))
    static_status = validate_static()
    if static_status != 0:
        return static_status
    if os.environ.get("SURE_XFORGE_STATIC_ONLY", "0") == "1":
        return 0

    sys.path.insert(0, str(MODEL_DIR))
    from model import ModelWrapper

    prompt_audio = max((MODEL_DIR / "fixture/zh").glob("*.mp3"), key=lambda path: path.stat().st_mtime)
    prompt_text = _load_prompt_text(prompt_audio)
    target_text = "空投认为继续往下跌的空间并不大啊，这个可以参考前期的低位一万四千七百一十元每吨。"
    output_path = MODEL_DIR / "artifacts/outputs/f5_tts_emilia_smoke.wav"

    wrapper = ModelWrapper({"device": os.environ.get("DEVICE", "cuda:0")})
    _log("VALIDATE_IMPORT", "passed", "ModelWrapper imported.", health=wrapper.healthcheck())
    wrapper.load()
    _log("VALIDATE_LOAD", "passed", "F5-TTS model loaded.", health=wrapper.healthcheck())

    result = wrapper.predict(
        {
            "prompt_audio_path": str(prompt_audio),
            "prompt_text": prompt_text,
            "text": target_text,
            "output_path": str(output_path),
            "language": "zh",
            "seed": 1234,
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
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
