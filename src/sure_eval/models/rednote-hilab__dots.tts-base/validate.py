#!/usr/bin/env python3
"""Phase-1 local validation for rednote-hilab/dots.tts-base."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf


MODEL_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", MODEL_DIR / "artifacts"))
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


def _load_fixture() -> dict[str, str]:
    fixture_path = MODEL_DIR / "fixture/tts/gt.jsonl"
    if not fixture_path.exists():
        raise FileNotFoundError(f"Missing fixture metadata: {fixture_path}")
    with fixture_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                item = json.loads(line)
                audio_path = MODEL_DIR / item["prompt_audio"]
                return {
                    "text": item["target_text"],
                    "prompt_audio_path": str(audio_path),
                    "prompt_text": item["prompt_text"],
                    "language": item.get("language", "en"),
                }
    raise ValueError(f"No fixture rows in {fixture_path}")


def main() -> int:
    os.environ.setdefault("HF_HOME", str(MODEL_DIR / ".runtime/hf-home"))
    os.environ.setdefault("HF_HUB_CACHE", str(MODEL_DIR / ".runtime/hf-home/hub"))
    os.environ.setdefault("MPLCONFIGDIR", str(MODEL_DIR / ".runtime/matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(MODEL_DIR / ".runtime/xdg-cache"))

    for required in ["model.spec.yaml", "model.py", "server.py", "__init__.py", "artifacts/backend_choice.json", "artifacts/build_plan.json"]:
        if not (MODEL_DIR / required).exists():
            _log("VALIDATE_SPEC", "failed", f"Missing required artifact: {required}")
            return 2
    _log("VALIDATE_SPEC", "passed", "Static phase-1 artifacts are present.")

    sys.path.insert(0, str(MODEL_DIR))
    try:
        from model import ModelWrapper

        _log("VALIDATE_IMPORT", "passed", "ModelWrapper imported.")
        wrapper = ModelWrapper({"device": os.environ.get("DEVICE", "cuda:0")})
        wrapper.load()
        _log("VALIDATE_LOAD", "passed", "DotsTtsRuntime loaded.", health=wrapper.healthcheck())

        fixture = _load_fixture()
        output_path = ARTIFACTS_DIR / "outputs/dots_tts_base_smoke.wav"
        result = wrapper.predict(
            {
                **fixture,
                "output_path": str(output_path),
                "num_steps": 10,
                "guidance_scale": 1.2,
                "max_generate_length": 900,
            }
        )
        output = Path(result.audio_path)
        if not output.exists() or output.stat().st_size <= 0:
            _log("VALIDATE_INFER", "failed", f"Missing or empty output audio: {output}")
            return 4
        data, sample_rate = sf.read(output)
        _log(
            "VALIDATE_INFER",
            "passed",
            "TTS inference completed and produced a nonempty audio file.",
            audio_path=str(output),
            file_size=output.stat().st_size,
            sample_rate=int(sample_rate),
            num_samples=int(len(data)),
        )
        if len(data) <= 0:
            _log("VALIDATE_CONTRACT", "failed", "Output audio has zero samples.", audio_path=str(output))
            return 5
        sample_output = {
            **result.to_dict(),
            "file_size": output.stat().st_size,
            "prompt_audio_path": fixture["prompt_audio_path"],
            "prompt_text": fixture["prompt_text"],
        }
        (ARTIFACTS_DIR / "sample_output.json").write_text(json.dumps(sample_output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _log(
            "VALIDATE_CONTRACT",
            "passed",
            "TTS output satisfies audio_path contract.",
            audio_path=str(output),
            sample_rate=int(sample_rate),
            num_samples=int(len(data)),
            result=sample_output,
        )
        return 0
    except Exception as exc:
        _log("VALIDATION_EXCEPTION", "failed", f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
