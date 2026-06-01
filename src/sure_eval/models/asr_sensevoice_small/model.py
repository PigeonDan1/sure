from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
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
    raw: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_sensevoice_text(text: str) -> tuple[str, str | None]:
    """Parse SenseVoice output format: <|lang|><|emotion|><|event|><|itn|>text"""
    # Extract language tag
    lang_match = re.search(r"<\|(\w{2,3})\|>", text)
    language = lang_match.group(1) if lang_match else None

    # Remove all tags like <|en|>, <|EMO_UNKNOWN|>, <|Speech|>, <|woitn|>
    clean_text = re.sub(r"<\|[^|]+\|>", "", text).strip()
    return clean_text, language


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_id = self.config.get("model_id") or os.environ.get(
            "MODEL_ID", "iic/SenseVoiceSmall"
        )
        self.device = self.config.get("device") or os.environ.get("DEVICE", "auto")
        model_path = self.config.get("model_path") or os.environ.get(
            "SENSEVOICE_MODEL_PATH",
            ".runtime/modelscope_cache/models/iic/SenseVoiceSmall",
        )
        self.model_path = self._resolve_model_relative_path(model_path)
        self._model = None
        self._resolved_device: str | None = None

    def _resolve_model_relative_path(self, path_value: str | os.PathLike[str]) -> str:
        path = Path(path_value).expanduser()
        if path.is_absolute():
            return str(path)
        return str((Path(__file__).resolve().parent / path).resolve())

    def _resolve_device(self) -> str:
        if self.device == "auto":
            try:
                import torch

                return "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                return "cpu"
        return self.device

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            from funasr import AutoModel

            self._resolved_device = self._resolve_device()
            self._model = AutoModel(
                model=self.model_path,
                device=self._resolved_device,
                disable_update=True,
            )
        except Exception as exc:
            raise ModelLoadError(
                f"Failed to load SenseVoice model {self.model_id}: {exc}"
            ) from exc

    def predict(self, input_data: str) -> TranscriptionResult:
        if self._model is None:
            self.load()
        try:
            raw_result = self._model.generate(
                input=input_data,
                language="auto",
            )
            if not isinstance(raw_result, list):
                raise InferenceError(
                    f"Expected list output from model.generate, got {type(raw_result).__name__}"
                )

            if not raw_result:
                return TranscriptionResult(text="", raw=[])

            # Use first result
            first = raw_result[0]
            raw_text = first.get("text", "")
            clean_text, detected_lang = _parse_sensevoice_text(raw_text)

            return TranscriptionResult(
                text=clean_text,
                language=detected_lang,
                raw=raw_result,
            )
        except Exception as exc:
            if isinstance(exc, InferenceError):
                raise
            raise InferenceError(
                f"Inference failed for SenseVoice model {self.model_id}: {exc}"
            ) from exc

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": "Model loaded" if self._model is not None else "Model not loaded",
            "model_loaded": self._model is not None,
            "model_id": self.model_id,
            "device": self.device,
            "resolved_device": self._resolved_device or self._resolve_device(),
            "model_path": self.model_path,
        }
