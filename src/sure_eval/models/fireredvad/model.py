from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class VADResult:
    timestamps: list[list[float]]
    dur: float | None = None
    wav_path: str | None = None

    def __post_init__(self) -> None:
        if not self.timestamps:
            raise ValueError("timestamps must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class ModelWrapper:
    """Thin wrapper around the validated FireRedVAD non-streaming VAD path."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_root = Path(__file__).resolve().parent
        self.model_dir = Path(
            self.config.get(
                "model_dir", self.model_root / "pretrained_models/FireRedVAD/VAD"
            )
        )
        self.use_gpu = bool(self.config.get("use_gpu", False))
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from fireredvad import FireRedVad, FireRedVadConfig

        vad_config = FireRedVadConfig(
            use_gpu=self.use_gpu,
            smooth_window_size=int(self.config.get("smooth_window_size", 5)),
            speech_threshold=float(self.config.get("speech_threshold", 0.4)),
            min_speech_frame=int(self.config.get("min_speech_frame", 20)),
            max_speech_frame=int(self.config.get("max_speech_frame", 2000)),
            min_silence_frame=int(self.config.get("min_silence_frame", 20)),
            merge_silence_frame=int(self.config.get("merge_silence_frame", 0)),
            extend_speech_frame=int(self.config.get("extend_speech_frame", 0)),
            chunk_max_frame=int(self.config.get("chunk_max_frame", 30000)),
        )
        self._model = FireRedVad.from_pretrained(str(self.model_dir), vad_config)

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": f"model_dir={self.model_dir}",
            "model_loaded": self._model is not None,
        }

    def predict(self, input_data: str | Path) -> VADResult:
        if self._model is None:
            self.load()
        audio_path = Path(input_data).resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Input audio not found: {audio_path}")
        raw_result, _ = self._model.detect(str(audio_path))
        timestamps = [list(pair) for pair in raw_result.get("timestamps", [])]
        return VADResult(
            timestamps=timestamps,
            dur=raw_result.get("dur"),
            wav_path=raw_result.get("wav_path"),
        )


def contract_result_to_json(result: VADResult) -> str:
    return result.to_json()
