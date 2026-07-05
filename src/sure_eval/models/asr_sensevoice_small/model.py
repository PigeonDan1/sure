from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

MODEL_DIR = Path(__file__).resolve().parent


def _clean_sensevoice_text(text: str) -> tuple[str, str | None]:
    lang_match = re.search(r"<\|(\w{2,3})\|>", text)
    language = lang_match.group(1) if lang_match else None
    clean = re.sub(r"<\|[^|]+\|>", "", text).strip()
    return clean, language


class ModelWrapper:
    def __init__(self, model_root: str | Path | None = None, device: str | None = None):
        self.model_root = Path(model_root or MODEL_DIR).resolve()
        self.device = device or os.environ.get("DEVICE", "auto")
        self.model_path = os.environ.get(
            "SENSEVOICE_MODEL_PATH",
            str(self.model_root / ".runtime" / "modelscope_cache" / "models" / "iic" / "SenseVoiceSmall"),
        )
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
        from funasr import AutoModel

        self._resolved_device = self._resolve_device()
        self._model = AutoModel(model=self.model_path, device=self._resolved_device, disable_update=True)

    def predict(self, request: dict[str, Any]) -> dict[str, Any]:
        audio_path = request.get("audio_path")
        if not audio_path:
            raise ValueError("request.audio_path is required")
        self.load()
        raw = self._model.generate(input=str(audio_path), language=request.get("language", "auto"))
        if not isinstance(raw, list):
            raise RuntimeError(f"Expected list output from FunASR, got {type(raw).__name__}")
        raw_text = str(raw[0].get("text", "")) if raw else ""
        text, language = _clean_sensevoice_text(raw_text)
        return {"text": text, "language": language, "raw": raw}

    def health(self) -> dict[str, Any]:
        return {
            "loaded": self._model is not None,
            "device": self.device,
            "resolved_device": self._resolved_device or self._resolve_device(),
            "model_path": self.model_path,
        }


SenseVoiceSmallReonboardModel = ModelWrapper
