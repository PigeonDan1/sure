from __future__ import annotations

import os
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
        self.model_id = self.config.get("model_id") or os.environ.get(
            "MODEL_ID", "openai/whisper-large-v3-turbo"
        )
        self.device = self.config.get("device") or os.environ.get("DEVICE", "cpu")
        self.hf_home = self.config.get("hf_home") or os.environ.get("HF_HOME")
        self.return_timestamps = bool(
            self.config.get("return_timestamps", False)
            or os.environ.get("RETURN_TIMESTAMPS", "").lower() in {"1", "true", "yes"}
        )
        self._processor = None
        self._model = None
        self._pipeline = None

    def load(self) -> None:
        if self._pipeline is not None:
            return
        if self.hf_home:
            os.environ["HF_HOME"] = self.hf_home
        try:
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

            self._processor = AutoProcessor.from_pretrained(self.model_id)
            self._model = AutoModelForSpeechSeq2Seq.from_pretrained(self.model_id)
            self._pipeline = pipeline(
                "automatic-speech-recognition",
                model=self._model,
                tokenizer=self._processor.tokenizer,
                feature_extractor=self._processor.feature_extractor,
                device=self.device,
            )
        except Exception as exc:
            raise ModelLoadError(f"Failed to load {self.model_id}: {exc}") from exc

    def predict(self, input_data: str) -> TranscriptionResult:
        if self._pipeline is None:
            self.load()
        try:
            kwargs: dict[str, Any] = {}
            if self.return_timestamps:
                kwargs["return_timestamps"] = True
            raw_result = self._pipeline(input_data, **kwargs)
            if not isinstance(raw_result, dict):
                raise InferenceError(
                    f"Expected dict output from pipeline, got {type(raw_result).__name__}"
                )
            return TranscriptionResult(
                text=raw_result.get("text", ""),
                language=raw_result.get("language"),
                segments=raw_result.get("chunks"),
            )
        except Exception as exc:
            if isinstance(exc, InferenceError):
                raise
            raise InferenceError(f"Inference failed for {self.model_id}: {exc}") from exc

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._pipeline is not None else "loading",
            "message": "Model loaded" if self._pipeline is not None else "Model not loaded",
            "model_loaded": self._pipeline is not None,
            "model_id": self.model_id,
            "device": self.device,
        }
