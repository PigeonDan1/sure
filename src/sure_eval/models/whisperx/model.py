from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass
class TranscriptionResult:
    text: str
    language: str | None = None
    timestamps: list[dict[str, Any]] | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "segments": self.timestamps or [],
            "confidence": self.confidence,
        }


class ModelWrapper:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        model_path: str | None = None,
        device: str | None = None,
    ) -> None:
        self.config = config or {}
        requested_arch = model_path
        if requested_arch and "/" in requested_arch:
            requested_arch = None
        self.model_arch = (
            self.config.get("model_arch")
            or os.environ.get("MODEL_ARCH")
            or requested_arch
            or "small"
        )
        self.device = device or self.config.get("device") or os.environ.get("DEVICE", "cpu")
        self.vad_method = self.config.get("vad_method") or os.environ.get(
            "VAD_METHOD", "pyannote"
        )
        self.hf_home = self.config.get("hf_home") or os.environ.get("HF_HOME")
        self.xdg_cache_home = self.config.get("xdg_cache_home") or os.environ.get(
            "XDG_CACHE_HOME"
        )
        self.mplconfigdir = (
            self.config.get("mplconfigdir")
            or os.environ.get("MPLCONFIGDIR")
            or str(Path(tempfile.gettempdir()) / "sure_eval_whisperx_mpl")
        )
        self._model = None
        self._resolved_device: str | None = None

    def _configure_runtime(self) -> None:
        if self.hf_home:
            os.environ["HF_HOME"] = self.hf_home
        if self.xdg_cache_home:
            os.environ["XDG_CACHE_HOME"] = self.xdg_cache_home
        if self.mplconfigdir:
            Path(self.mplconfigdir).mkdir(parents=True, exist_ok=True)
            os.environ["MPLCONFIGDIR"] = self.mplconfigdir
        if self.device == "cpu":
            os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

    def _resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"

    def load(self) -> None:
        if self._model is not None:
            return

        self._configure_runtime()
        try:
            with contextlib.redirect_stdout(sys.stderr):
                import whisperx

                resolved_device = self._resolve_device()
                print(f"Loading WhisperX model: {self.model_arch}", file=sys.stderr)
                load_kwargs: dict[str, Any] = {"vad_method": self.vad_method}
                if resolved_device == "cpu":
                    load_kwargs["compute_type"] = "float32"
                self._model = whisperx.load_model(self.model_arch, resolved_device, **load_kwargs)
                self._resolved_device = resolved_device
                print(f"Model loaded on {resolved_device}", file=sys.stderr)
        except Exception as exc:
            raise ModelLoadError(f"Failed to load WhisperX model: {exc}") from exc

    def _normalize_segments(self, raw_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_segments: list[dict[str, Any]] = []
        for segment in raw_segments:
            normalized_segments.append(
                {
                    "start": segment.get("start", 0),
                    "end": segment.get("end", 0),
                    "text": (segment.get("text") or "").strip(),
                }
            )
        return normalized_segments

    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None = None,
        return_timestamps: bool = False,
    ) -> TranscriptionResult:
        if self._model is None:
            self.load()
        self._configure_runtime()

        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        try:
            with contextlib.redirect_stdout(sys.stderr):
                import whisperx

                audio = whisperx.load_audio(str(path))
                transcribe_kwargs: dict[str, Any] = {}
                if language and language != "auto":
                    transcribe_kwargs["language"] = language
                if (self._resolved_device or self.device) == "cpu":
                    transcribe_kwargs["batch_size"] = 1
                raw_result = self._model.transcribe(audio, **transcribe_kwargs)
            if not isinstance(raw_result, dict):
                raise InferenceError(
                    f"Expected dict output from whisperx.transcribe, got {type(raw_result).__name__}"
                )

            raw_segments = raw_result.get("segments") or []
            normalized_segments = self._normalize_segments(raw_segments)
            text = (raw_result.get("text") or "").strip()
            if not text:
                text = " ".join(
                    segment["text"] for segment in normalized_segments if segment["text"]
                ).strip()

            return TranscriptionResult(
                text=text,
                language=raw_result.get("language") or language,
                timestamps=normalized_segments if return_timestamps else None,
                confidence=None,
            )
        except Exception as exc:
            if isinstance(exc, InferenceError):
                raise
            raise InferenceError(f"WhisperX inference failed: {exc}") from exc

    def predict(self, input_data: str) -> dict[str, Any]:
        return self.transcribe(input_data, return_timestamps=True).to_dict()

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": "Model loaded" if self._model is not None else "Model not loaded",
            "model_loaded": self._model is not None,
            "model_arch": self.model_arch,
            "device": self.device,
            "resolved_device": self._resolved_device,
            "vad_method": self.vad_method,
        }


ASRModel = ModelWrapper
