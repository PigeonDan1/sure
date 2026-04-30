from __future__ import annotations

import contextlib
import os
import shutil
import sys
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
        self.model_id = model_path or self.config.get("model_id") or os.environ.get(
            "MODEL_ID", "turbo"
        )
        self.device = device or self.config.get("device") or os.environ.get("DEVICE", "cpu")
        self.download_root = self.config.get("download_root") or os.environ.get(
            "WHISPER_DOWNLOAD_ROOT"
        )
        self.ffmpeg_binary = self.config.get("ffmpeg_binary") or os.environ.get(
            "FFMPEG_BINARY"
        )
        self._model = None
        self._resolved_device: str | None = None

    def _configure_runtime(self) -> None:
        if self.device == "cpu":
            os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
        if not self.ffmpeg_binary:
            return
        os.environ["FFMPEG_BINARY"] = self.ffmpeg_binary
        ffmpeg_dir = os.path.dirname(self.ffmpeg_binary)
        current_path = os.environ.get("PATH", "")
        path_entries = current_path.split(os.pathsep) if current_path else []
        if ffmpeg_dir and ffmpeg_dir not in path_entries:
            os.environ["PATH"] = (
                f"{ffmpeg_dir}{os.pathsep}{current_path}" if current_path else ffmpeg_dir
            )

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
                import whisper

                resolved_device = self._resolve_device()
                print(f"Loading Whisper model: {self.model_id}", file=sys.stderr)
                load_kwargs: dict[str, Any] = {"device": resolved_device}
                if self.download_root:
                    load_kwargs["download_root"] = self.download_root
                self._model = whisper.load_model(self.model_id, **load_kwargs)
                self._resolved_device = resolved_device
                print(f"Model loaded on {resolved_device}", file=sys.stderr)
            if self.ffmpeg_binary and shutil.which("ffmpeg") is None:
                raise ModelLoadError(
                    f"Configured ffmpeg binary is not executable via PATH: {self.ffmpeg_binary}"
                )
        except ModelLoadError:
            raise
        except Exception as exc:
            raise ModelLoadError(f"Failed to load Whisper model {self.model_id}: {exc}") from exc

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

        lang_code = language if language and language != "auto" else None
        options: dict[str, Any] = {}
        if lang_code:
            options["language"] = lang_code
        if (self._resolved_device or self.device) == "cpu":
            options["fp16"] = False

        try:
            with contextlib.redirect_stdout(sys.stderr):
                raw_result = self._model.transcribe(str(path), **options)
            if not isinstance(raw_result, dict):
                raise InferenceError(
                    f"Expected dict output from whisper.transcribe, got {type(raw_result).__name__}"
                )

            raw_segments = raw_result.get("segments") or []
            timestamps = None
            if return_timestamps:
                timestamps = [
                    {
                        "start": segment.get("start", 0),
                        "end": segment.get("end", 0),
                        "text": (segment.get("text") or "").strip(),
                    }
                    for segment in raw_segments
                ]

            return TranscriptionResult(
                text=(raw_result.get("text") or "").strip(),
                language=raw_result.get("language") or lang_code,
                timestamps=timestamps,
                confidence=None,
            )
        except Exception as exc:
            if isinstance(exc, InferenceError):
                raise
            raise InferenceError(
                f"Inference failed for Whisper model {self.model_id}: {exc}"
            ) from exc

    def predict(self, input_data: str) -> dict[str, Any]:
        return self.transcribe(input_data, return_timestamps=True).to_dict()

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": "Model loaded" if self._model is not None else "Model not loaded",
            "model_loaded": self._model is not None,
            "model_id": self.model_id,
            "device": self.device,
            "resolved_device": self._resolved_device,
            "ffmpeg_binary": self.ffmpeg_binary or shutil.which("ffmpeg"),
            "download_root": self.download_root,
        }


ASRModel = ModelWrapper
