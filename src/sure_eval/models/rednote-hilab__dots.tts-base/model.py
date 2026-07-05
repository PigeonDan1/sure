"""SURE wrapper for rednote-hilab/dots.tts-base."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent


@dataclass
class TTSResult:
    text: str
    audio_path: str
    language: str = "auto"
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.model_path = Path(
            self.config.get("model_path")
            or os.environ.get("MODEL_PATH")
            or _weights_manifest_path()
            or MODEL_DIR / "checkpoints"
        )
        self.device = self.config.get("device") or os.environ.get("DEVICE") or "cuda:0"
        self.precision = self.config.get("precision") or os.environ.get("PRECISION") or "float16"
        self.optimize = bool(self.config.get("optimize", False))
        self.max_generate_length = int(self.config.get("max_generate_length") or os.environ.get("MAX_GENERATE_LENGTH") or 500)
        self.model_loaded = False
        self._runtime = None

    def load(self) -> None:
        os.environ.setdefault("HF_HOME", str(MODEL_DIR / ".runtime/hf-home"))
        os.environ.setdefault("HF_HUB_CACHE", str(MODEL_DIR / ".runtime/hf-home/hub"))
        os.environ.setdefault("MPLCONFIGDIR", str(MODEL_DIR / ".runtime/matplotlib"))
        os.environ.setdefault("XDG_CACHE_HOME", str(MODEL_DIR / ".runtime/xdg-cache"))
        if not self.model_path.exists():
            raise FileNotFoundError(f"Missing dots.tts checkpoint directory: {self.model_path}")

        from dots_tts.runtime import DotsTtsRuntime

        self._runtime = DotsTtsRuntime.from_pretrained(
            str(self.model_path),
            precision=self.precision,
            optimize=self.optimize,
            max_generate_length=self.max_generate_length,
        )
        self.model_loaded = True

    def predict(self, input_data: Any) -> TTSResult:
        payload = input_data if isinstance(input_data, dict) else {"text": str(input_data)}
        text = payload.get("text")
        prompt_audio_path = payload.get("prompt_audio_path") or payload.get("prompt_audio")
        prompt_text = payload.get("prompt_text") or ""
        if not text:
            raise ValueError("text is required")
        if not prompt_audio_path:
            raise ValueError("prompt_audio_path is required for the configured phase-1 fixture")
        if not self.model_loaded:
            self.load()
        assert self._runtime is not None

        output_path = Path(payload.get("output_path") or MODEL_DIR / "artifacts/outputs/dots_tts_base_smoke.wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = self._runtime.generate(
            text=text,
            prompt_audio_path=str(prompt_audio_path),
            prompt_text=prompt_text,
            language=payload.get("language"),
            num_steps=int(payload.get("num_steps", 10)),
            guidance_scale=float(payload.get("guidance_scale", 1.2)),
        )

        import soundfile as sf

        audio = result["audio"].float().cpu().squeeze().numpy()
        peak = float(abs(audio).max()) if audio.size else 0.0
        if peak > 0.98:
            audio = audio * (0.98 / peak)
        sample_rate = int(result["sample_rate"])
        sf.write(output_path, audio, sample_rate)
        return TTSResult(
            text=text,
            audio_path=str(output_path),
            language=str(payload.get("language") or "auto"),
            raw={
                "sample_rate": sample_rate,
                "num_samples": int(len(audio)),
                "model_path": str(self.model_path),
                "peak_before_write": peak,
            },
        )

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "loaded" if self.model_loaded else "ready",
            "model_loaded": self.model_loaded,
            "model_path": str(self.model_path),
            "device": self.device,
            "precision": self.precision,
            "max_generate_length": self.max_generate_length,
        }

    def health(self) -> dict[str, Any]:
        return self.healthcheck()


def _weights_manifest_path() -> str | None:
    manifest_path = MODEL_DIR / "artifacts/weights_manifest.json"
    if not manifest_path.exists():
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    resolved = payload.get("resolved_local_model_path")
    if resolved and Path(resolved).exists():
        return resolved
    return None
