from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass
class SpeakerVerificationResult:
    similarity_score: float
    enrollment_audio: str
    trial_audio: str
    model_name: str
    device: str
    cache_dir: str

    def __post_init__(self) -> None:
        if not isinstance(self.similarity_score, (int, float)):
            raise ValueError("similarity_score must be numeric")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class ModelWrapper:
    """Thin wrapper around the validated WeSpeaker english similarity path."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_root = Path(__file__).resolve().parent
        self.cache_dir = Path(
            self.config.get(
                "cache_dir", self.model_root / "pretrained_models" / "wespeaker"
            )
        )
        self.model_name = str(self.config.get("model_name", "english"))
        self.device = str(self.config.get("device", "cpu"))
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        os.environ["WESPEAKER_HOME"] = str(self.cache_dir)
        try:
            import wespeaker

            model = wespeaker.load_model(self.model_name)
            model.set_device(self.device)
        except Exception as exc:  # noqa: BLE001
            raise ModelLoadError(str(exc)) from exc
        self._model = model

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": f"model_name={self.model_name} device={self.device}",
            "model_loaded": self._model is not None,
            "cache_dir": str(self.cache_dir),
            "cache_present": (self.cache_dir / self.model_name).exists(),
        }

    def predict(self, input_data: Any) -> SpeakerVerificationResult:
        if self._model is None:
            self.load()
        enrollment_audio, trial_audio = self._normalize_input(input_data)
        try:
            score = self._model.compute_similarity(enrollment_audio, trial_audio)
        except Exception as exc:  # noqa: BLE001
            raise InferenceError(str(exc)) from exc
        return SpeakerVerificationResult(
            similarity_score=float(score),
            enrollment_audio=enrollment_audio,
            trial_audio=trial_audio,
            model_name=self.model_name,
            device=self.device,
            cache_dir=str(self.cache_dir),
        )

    def _normalize_input(self, input_data: Any) -> tuple[str, str]:
        if isinstance(input_data, dict):
            enrollment_audio = input_data.get("enrollment_audio")
            trial_audio = input_data.get("trial_audio")
        elif isinstance(input_data, (list, tuple)) and len(input_data) == 2:
            enrollment_audio, trial_audio = input_data
        else:
            raise ValueError(
                "input_data must be a dict with enrollment_audio/trial_audio or a 2-item sequence"
            )
        if not enrollment_audio or not trial_audio:
            raise ValueError("Both enrollment_audio and trial_audio are required")
        enrollment_path = Path(enrollment_audio).resolve()
        trial_path = Path(trial_audio).resolve()
        if not enrollment_path.exists():
            raise FileNotFoundError(f"Enrollment audio not found: {enrollment_path}")
        if not trial_path.exists():
            raise FileNotFoundError(f"Trial audio not found: {trial_path}")
        return str(enrollment_path), str(trial_path)


def contract_result_to_json(result: SpeakerVerificationResult) -> str:
    return result.to_json()
