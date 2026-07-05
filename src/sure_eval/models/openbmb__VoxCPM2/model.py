"""VoxCPM2 model-local wrapper for SURE-EVAL."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


MODEL_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_ID = "openbmb/VoxCPM2"


class ModelLoadError(RuntimeError):
    """Raised when VoxCPM2 cannot be loaded."""


class InferenceError(RuntimeError):
    """Raised when VoxCPM2 generation fails."""


@dataclass
class TTSAudioResult:
    audio: np.ndarray
    sample_rate: int
    text: str
    audio_path: str | None = None

    def to_summary(self) -> dict[str, Any]:
        return {
            "sample_rate": self.sample_rate,
            "num_samples": int(self.audio.size),
            "dtype": str(self.audio.dtype),
            "shape": list(self.audio.shape),
            "text": self.text,
            "audio_path": self.audio_path,
        }


class ModelWrapper:
    """Thin wrapper around the official ``voxcpm.VoxCPM`` API."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.model_id = self.config.get("model_id") or os.environ.get("MODEL_ID") or DEFAULT_MODEL_ID
        self.model_path = self.config.get("model_path") or os.environ.get("MODEL_PATH")
        self.device = self.config.get("device") or os.environ.get("DEVICE") or "auto"
        self.optimize = bool(self.config.get("optimize", False))
        self.load_denoiser = bool(self.config.get("load_denoiser", False))
        self._model = None
        self.model_loaded = False

    def _set_model_local_env(self) -> None:
        runtime_dir = MODEL_DIR / ".runtime"
        env_defaults = {
            "HF_HOME": runtime_dir / "hf-home",
            "HF_HUB_CACHE": runtime_dir / "hf-home" / "hub",
            "HUGGINGFACE_HUB_CACHE": runtime_dir / "hf-home" / "hub",
            "TRANSFORMERS_CACHE": runtime_dir / "hf-home" / "transformers",
            "MPLCONFIGDIR": runtime_dir / "matplotlib",
            "TMPDIR": runtime_dir / "tmp",
        }
        for key, path in env_defaults.items():
            path.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault(key, str(path.resolve()))

    def _snapshot_from_cache(self) -> str | None:
        cache_root = MODEL_DIR / ".runtime" / "hf-home" / "hub" / "models--openbmb--VoxCPM2" / "snapshots"
        if not cache_root.exists():
            return None
        snapshots = sorted(
            (path for path in cache_root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return str(snapshots[0].resolve()) if snapshots else None

    def _resolve_model_path(self) -> str:
        if self.model_path:
            candidate = Path(self.model_path)
            return str(candidate.resolve()) if candidate.exists() else self.model_path

        checkpoint_path = MODEL_DIR / "checkpoints" / "VoxCPM2"
        if checkpoint_path.exists() and any(checkpoint_path.iterdir()):
            return str(checkpoint_path.resolve())

        manifest_path = MODEL_DIR / "artifacts" / "weights_manifest.json"
        if manifest_path.exists():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            resolved = payload.get("resolved_local_model_path")
            if resolved and Path(resolved).exists():
                return str(Path(resolved).resolve())

        cached = self._snapshot_from_cache()
        if cached:
            return cached
        return self.model_id

    def load(self) -> None:
        if self.model_loaded:
            return
        self._set_model_local_env()

        try:
            from voxcpm import VoxCPM
        except Exception as exc:  # noqa: BLE001
            raise ModelLoadError(f"VoxCPM import failed: {exc}") from exc

        try:
            self._model = VoxCPM.from_pretrained(
                self._resolve_model_path(),
                load_denoiser=self.load_denoiser,
                optimize=self.optimize,
                device=self.device,
                cache_dir=str((MODEL_DIR / ".runtime" / "hf-home").resolve()),
            )
        except Exception as exc:  # noqa: BLE001
            raise ModelLoadError(f"VoxCPM.from_pretrained failed: {exc}") from exc
        self.model_loaded = True

    def predict(self, input_data: Any) -> TTSAudioResult:
        payload = input_data if isinstance(input_data, dict) else {"text": str(input_data)}
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        cfg_value = float(payload.get("cfg_value", 2.0))
        inference_timesteps = int(payload.get("inference_timesteps", 10))

        if not self.model_loaded:
            self.load()
        assert self._model is not None

        kwargs: dict[str, Any] = {
            "text": text,
            "cfg_value": cfg_value,
            "inference_timesteps": inference_timesteps,
        }
        if payload.get("prompt_wav_path"):
            kwargs["prompt_wav_path"] = payload["prompt_wav_path"]
        if payload.get("prompt_text"):
            kwargs["prompt_text"] = payload["prompt_text"]
        if payload.get("reference_wav_path"):
            kwargs["reference_wav_path"] = payload["reference_wav_path"]

        try:
            wav = self._model.generate(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise InferenceError(f"VoxCPM.generate failed: {exc}") from exc

        audio = np.asarray(wav)
        sample_rate = int(getattr(self._model.tts_model, "sample_rate"))
        audio_path = payload.get("audio_path")
        if audio_path:
            import soundfile as sf

            output_path = Path(audio_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(output_path), audio, sample_rate)
            audio_path = str(output_path)

        return TTSAudioResult(audio=audio, sample_rate=sample_rate, text=text, audio_path=audio_path)

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "loaded" if self.model_loaded else "ready",
            "model_loaded": self.model_loaded,
            "model_path": self._resolve_model_path(),
            "device": self.device,
            "optimize": self.optimize,
            "load_denoiser": self.load_denoiser,
        }

    health = healthcheck
