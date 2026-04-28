import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class ModelLoadError(RuntimeError):
    """Raised when the underlying Silero package cannot load the model."""


class InferenceError(RuntimeError):
    """Raised when minimal VAD inference fails."""


@dataclass
class VADOutput:
    """Unified JSON-serializable output for phase-1 VAD validation."""

    segments: List[Dict[str, float]]
    sample_rate: int
    audio_path: str
    audio_duration_sec: float
    model_backend: str
    error_code: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.model_backend:
            raise ValueError("model_backend must be non-empty")
        if self.audio_duration_sec <= 0:
            raise ValueError("audio_duration_sec must be positive")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True)


class ModelWrapper:
    """Thin wrapper over the pip package callable path used in phase-1."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        device: str = "cpu",
        onnx: bool = False,
    ) -> None:
        self.config = config or {}
        self.device = device
        self.onnx = onnx
        self.model = None
        self.model_backend = (
            "silero_vad_onnx_cpu" if onnx else "silero_vad_pytorch_jit"
        )

    def load(self) -> None:
        if self.device != "cpu":
            raise ModelLoadError("Phase-1 wrapper only supports CPU execution")
        if self.model is not None:
            return
        try:
            import torch
            from silero_vad import load_silero_vad

            torch.set_num_threads(1)
            self.model = load_silero_vad(onnx=self.onnx)
            self.model.eval()
        except Exception as exc:
            raise ModelLoadError(f"Failed to load Silero VAD: {exc}") from exc

    def predict(self, input_data: Any, sampling_rate: int = 16000) -> VADOutput:
        audio_path = Path(str(input_data)).expanduser().resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        self.load()

        try:
            from silero_vad import get_speech_timestamps, read_audio

            wav = read_audio(str(audio_path), sampling_rate=sampling_rate)
            segments = get_speech_timestamps(
                wav,
                self.model,
                sampling_rate=sampling_rate,
                return_seconds=True,
            )
            return VADOutput(
                segments=segments,
                sample_rate=sampling_rate,
                audio_path=str(audio_path),
                audio_duration_sec=float(len(wav) / sampling_rate),
                model_backend=self.model_backend,
                error_code=None,
            )
        except Exception as exc:
            raise InferenceError(f"Silero VAD inference failed: {exc}") from exc

    def healthcheck(self) -> Dict[str, Any]:
        return {
            "status": "ready" if self.model is not None else "loading",
            "message": "model loaded" if self.model is not None else "model not loaded",
            "model_loaded": self.model is not None,
            "device": self.device,
            "model_backend": self.model_backend,
        }


VADModel = ModelWrapper
VADResult = VADOutput


def predict_vad(audio_path: str, device: str = "cpu") -> VADOutput:
    return ModelWrapper(device=device).predict(audio_path)
