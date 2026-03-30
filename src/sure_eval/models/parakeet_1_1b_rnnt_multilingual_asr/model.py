from __future__ import annotations

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
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_id = self.config.get(
            "model_id", "nvidia/parakeet-1.1b-rnnt-multilingual-asr"
        )
        self._loaded = False

    def load(self) -> None:
        raise ModelLoadError(
            "Phase-1 runtime is blocked: no verified public NeMo/Hugging Face checkpoint "
            f"has been established for {self.model_id}, while the exact public evidence points "
            "to a GPU-only NVIDIA NIM deployment path instead."
        )

    def predict(self, input_data: str) -> TranscriptionResult:
        if not self._loaded:
            self.load()
        raise InferenceError(f"Prediction unavailable for unresolved model {self.model_id}")

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "error",
            "message": "Runtime blocked pending checkpoint clarification or human override",
            "model_loaded": False,
            "model_id": self.model_id,
        }
