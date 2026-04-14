from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import Any


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass
class TranscriptionResult:
    text: str
    language: str | None = None
    segments: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_id = self.config.get("model_id") or os.environ.get("MODEL_ID", "turbo")
        self.device = self.config.get("device") or os.environ.get("DEVICE", "cpu")
        self.download_root = self.config.get("download_root") or os.environ.get(
            "WHISPER_DOWNLOAD_ROOT"
        )
        self.ffmpeg_binary = self.config.get("ffmpeg_binary") or os.environ.get(
            "FFMPEG_BINARY"
        )
        self._model = None

    def _configure_runtime(self) -> None:
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

    def load(self) -> None:
        if self._model is not None:
            return
        self._configure_runtime()
        try:
            import whisper

            load_kwargs: dict[str, Any] = {"device": self.device}
            if self.download_root:
                load_kwargs["download_root"] = self.download_root
            self._model = whisper.load_model(self.model_id, **load_kwargs)
            if self.ffmpeg_binary and shutil.which("ffmpeg") is None:
                raise ModelLoadError(
                    f"Configured ffmpeg binary is not executable via PATH: {self.ffmpeg_binary}"
                )
        except ModelLoadError:
            raise
        except Exception as exc:
            raise ModelLoadError(f"Failed to load Whisper model {self.model_id}: {exc}") from exc

    def predict(self, input_data: str) -> TranscriptionResult:
        if self._model is None:
            self.load()
        self._configure_runtime()
        try:
            raw_result = self._model.transcribe(input_data, fp16=False)
            if not isinstance(raw_result, dict):
                raise InferenceError(
                    f"Expected dict output from whisper.transcribe, got {type(raw_result).__name__}"
                )
            return TranscriptionResult(
                text=raw_result.get("text", ""),
                language=raw_result.get("language"),
                segments=raw_result.get("segments"),
            )
        except Exception as exc:
            if isinstance(exc, InferenceError):
                raise
            raise InferenceError(
                f"Inference failed for Whisper model {self.model_id}: {exc}"
            ) from exc

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": "Model loaded" if self._model is not None else "Model not loaded",
            "model_loaded": self._model is not None,
            "model_id": self.model_id,
            "device": self.device,
            "ffmpeg_binary": self.ffmpeg_binary or shutil.which("ffmpeg"),
            "download_root": self.download_root,
        }
