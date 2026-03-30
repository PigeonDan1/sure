from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass
class WhisperXResult:
    segments: list[dict[str, Any]]
    language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.model_arch = self.config.get("model_arch") or os.environ.get("MODEL_ARCH", "small")
        self.device = self.config.get("device") or os.environ.get("DEVICE", "cpu")
        self.vad_method = self.config.get("vad_method") or os.environ.get("VAD_METHOD", "pyannote")
        self.hf_home = self.config.get("hf_home") or os.environ.get("HF_HOME")
        self.xdg_cache_home = self.config.get("xdg_cache_home") or os.environ.get("XDG_CACHE_HOME")
        self.mplconfigdir = self.config.get("mplconfigdir") or os.environ.get("MPLCONFIGDIR")
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return

        if self.hf_home:
            os.environ["HF_HOME"] = self.hf_home
        if self.xdg_cache_home:
            os.environ["XDG_CACHE_HOME"] = self.xdg_cache_home
        if self.mplconfigdir:
            os.environ["MPLCONFIGDIR"] = self.mplconfigdir

        try:
            import whisperx

            self._model = whisperx.load_model(self.model_arch, self.device, vad_method=self.vad_method)
        except Exception as exc:
            raise ModelLoadError(f"Failed to load WhisperX model: {exc}") from exc

    def predict(self, input_data: str) -> dict[str, Any]:
        if self._model is None:
            self.load()

        try:
            import whisperx

            audio = whisperx.load_audio(input_data)
            result = self._model.transcribe(audio)
            wrapped = WhisperXResult(
                segments=result.get("segments", []),
                language=result.get("language"),
            )
            return wrapped.to_dict()
        except Exception as exc:
            raise InferenceError(f"WhisperX inference failed: {exc}") from exc

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": "Model loaded" if self._model is not None else "Model not loaded",
            "model_loaded": self._model is not None,
            "model_arch": self.model_arch,
            "device": self.device,
            "vad_method": self.vad_method,
        }
