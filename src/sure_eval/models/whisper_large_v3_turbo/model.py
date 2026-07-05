from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

MODEL_DIR = Path(__file__).resolve().parent


class ModelWrapper:
    def __init__(self, model_root: str | Path | None = None, device: str | None = None):
        self.model_root = Path(model_root or MODEL_DIR).resolve()
        self.device = device or os.environ.get("DEVICE", "auto")
        self.model_id = os.environ.get("MODEL_ID", "turbo")
        self.download_root = os.environ.get("WHISPER_DOWNLOAD_ROOT", str(self.model_root / "checkpoints"))
        self._model = None
        self._resolved_device: str | None = None

    def _resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def load(self) -> None:
        if self._model is not None:
            return
        import whisper

        self._resolved_device = self._resolve_device()
        self._model = whisper.load_model(self.model_id, download_root=self.download_root, device=self._resolved_device)

    def predict(self, request: dict[str, Any]) -> dict[str, Any]:
        audio_path = request.get("audio_path")
        if not audio_path:
            raise ValueError("request.audio_path is required")
        self.load()
        fp16 = bool((self._resolved_device or "").startswith("cuda"))
        raw = self._model.transcribe(str(audio_path), fp16=fp16, language=request.get("language"))
        return {
            "text": str(raw.get("text", "")).strip(),
            "language": raw.get("language"),
            "segments": raw.get("segments"),
        }

    def health(self) -> dict[str, Any]:
        return {
            "loaded": self._model is not None,
            "model_id": self.model_id,
            "device": self.device,
            "resolved_device": self._resolved_device or self._resolve_device(),
            "download_root": self.download_root,
            "ffmpeg": shutil.which("ffmpeg"),
        }


WhisperLargeV3TurboReonboardModel = ModelWrapper
