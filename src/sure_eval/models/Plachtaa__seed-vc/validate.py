#!/usr/bin/env python3
"""Local validation for Plachtaa/seed-vc V2 voice conversion."""

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
        ".runtime/source/seed-vc/inference_v2.py",
        ".runtime/source/seed-vc/configs/v2/vc_wrapper.yaml",
    ]
    missing = [item for item in required if not (MODEL_DIR / item).exists()]
    fixture_files = sorted((MODEL_DIR / "fixture/zh").glob("*.mp3"))
    if len(fixture_files) < 2:
        missing.append("fixture/zh/*.mp3 (need at least two files)")
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

    source_audio = MODEL_DIR / "fixture/zh/ZH_B00000_S00000_W000002.mp3"
    reference_audio = MODEL_DIR / "fixture/zh/ZH_B00001_S00000_W000000.mp3"
    if not source_audio.exists() or not reference_audio.exists():
        files = sorted((MODEL_DIR / "fixture/zh").glob("*.mp3"))
        source_audio, reference_audio = files[0], files[1]
    output_path = MODEL_DIR / "artifacts/outputs/seed_vc_v2_smoke.wav"

    wrapper = ModelWrapper({"device": os.environ.get("DEVICE", "cuda"), "diffusion_steps": int(os.environ.get("DIFFUSION_STEPS", "4"))})
    _log("VALIDATE_IMPORT", "passed", "ModelWrapper imported.", health=wrapper.healthcheck())
    wrapper.load()
    _log("VALIDATE_LOAD", "passed", "Seed-VC V2 model loaded.", health=wrapper.healthcheck())

    result = wrapper.predict(
        {
            "source_audio_path": str(source_audio),
            "reference_audio_path": str(reference_audio),
            "output_path": str(output_path),
            "diffusion_steps": int(os.environ.get("DIFFUSION_STEPS", "4")),
            "convert_style": os.environ.get("CONVERT_STYLE", "0") == "1",
        }
    )
    _log("VALIDATE_INFER", "passed", "Seed-VC V2 inference completed.", result=result.to_dict())
    output = Path(result.audio_path)
    if not output.exists() or output.stat().st_size <= 0:
        _log("VALIDATE_INFER", "failed", f"Missing or empty output audio: {output}")
        return 4
    import soundfile as sf

    data, sample_rate = sf.read(output)
    if len(data) == 0:
        _log("VALIDATE_CONTRACT", "failed", "Output audio has zero samples.", audio_path=str(output))
        return 5
    _log(
        "VALIDATE_CONTRACT",
        "passed",
        "VC output satisfies SURE audio_path contract.",
        audio_path=str(output),
        sample_rate=int(sample_rate),
        num_samples=int(len(data)),
        result=result.to_dict(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
