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

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_id = self.config.get("model_id") or os.environ.get(
            "MODEL_ID", "nvidia/parakeet-rnnt-1.1b"
        )
        self.device = self.config.get("device") or os.environ.get("DEVICE", "cpu")
        self.tmpdir = self.config.get("tmpdir") or os.environ.get("TMPDIR")
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        if self.tmpdir:
            os.environ["TMPDIR"] = self.tmpdir
        if self.device == "cpu":
            os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
        try:
            import nemo.collections.asr as nemo_asr
            import torch

            model = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained(
                model_name=self.model_id,
            )
            if self.device == "cuda" and torch.cuda.is_available():
                model = model.to("cuda")
            self._model = model
        except Exception as exc:
            raise ModelLoadError(f"Failed to load {self.model_id}: {exc}") from exc

    def predict(self, input_data: str) -> TranscriptionResult:
        if self._model is None:
            self.load()
        try:
            outputs = self._model.transcribe([input_data])
            if not outputs:
                raise InferenceError("transcribe returned no outputs")
            first = outputs[0]
            text = first.text if hasattr(first, "text") else str(first)
            return TranscriptionResult(text=text, language="en")
        except Exception as exc:
            if isinstance(exc, InferenceError):
                raise
            raise InferenceError(f"Inference failed for {self.model_id}: {exc}") from exc

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": "Model loaded" if self._model is not None else "Model not loaded",
            "model_loaded": self._model is not None,
            "model_id": self.model_id,
            "device": self.device,
            "tmpdir": self.tmpdir,
        }
