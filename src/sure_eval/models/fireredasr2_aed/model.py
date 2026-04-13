from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass
class FireRedASR2AEDResult:
    uttid: str
    text: str
    confidence: float | None = None
    dur_s: float | None = None
    rtf: str | None = None
    wav: str | None = None

    def __post_init__(self) -> None:
        if not self.text or not self.text.strip():
            raise ValueError("text must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    """Thin wrapper around the validated standalone FireRedAsr2 AED path."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_root = Path(__file__).resolve().parent
        self.upstream_root = Path(self.config.get("upstream_root", self.model_root / "upstream" / "FireRedASR2S"))
        self.weights_dir = Path(
            self.config.get("weights_dir", self.model_root / "pretrained_models" / "FireRedASR2-AED")
        )
        self.use_gpu = bool(self.config.get("use_gpu", False))
        self.use_half = bool(self.config.get("use_half", False))
        self._model = None
        self._import_ready = False

    def _ensure_import_path(self) -> None:
        if self._import_ready:
            return
        if not self.upstream_root.exists():
            raise ModelLoadError(f"Upstream repo not found: {self.upstream_root}")
        sys.path.insert(0, str(self.upstream_root))
        self._import_ready = True

    def load(self) -> None:
        if self._model is not None:
            return
        self._ensure_import_path()
        if not self.weights_dir.exists():
            raise ModelLoadError(f"Weights directory not found: {self.weights_dir}")
        try:
            from fireredasr2s.fireredasr2 import FireRedAsr2, FireRedAsr2Config

            asr_config = FireRedAsr2Config(
                use_gpu=self.use_gpu,
                use_half=self.use_half,
                beam_size=int(self.config.get("beam_size", 3)),
                nbest=int(self.config.get("nbest", 1)),
                decode_max_len=int(self.config.get("decode_max_len", 0)),
                softmax_smoothing=float(self.config.get("softmax_smoothing", 1.25)),
                aed_length_penalty=float(self.config.get("aed_length_penalty", 0.6)),
                eos_penalty=float(self.config.get("eos_penalty", 1.0)),
                return_timestamp=bool(self.config.get("return_timestamp", False)),
            )
            self._model = FireRedAsr2.from_pretrained("aed", str(self.weights_dir), asr_config)
        except Exception as exc:
            raise ModelLoadError(f"Failed to load FireRedASR2-AED: {exc}") from exc

    def predict(self, input_data: str | Path) -> dict[str, Any]:
        if self._model is None:
            self.load()
        audio_path = Path(input_data).resolve()
        if not audio_path.exists():
            raise InferenceError(f"Input audio not found: {audio_path}")
        try:
            uttid = audio_path.stem
            outputs = self._model.transcribe([uttid], [str(audio_path)])
            if not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], dict):
                raise InferenceError("Unexpected FireRedASR2-AED output shape")
            payload = outputs[0]
            result = FireRedASR2AEDResult(
                uttid=str(payload["uttid"]),
                text=str(payload["text"]),
                confidence=float(payload["confidence"]) if payload.get("confidence") is not None else None,
                dur_s=float(payload["dur_s"]) if payload.get("dur_s") is not None else None,
                rtf=str(payload["rtf"]) if payload.get("rtf") is not None else None,
                wav=str(payload["wav"]) if payload.get("wav") is not None else str(audio_path),
            )
            return result.to_dict()
        except InferenceError:
            raise
        except Exception as exc:
            raise InferenceError(f"FireRedASR2-AED inference failed: {exc}") from exc

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": f"weights_dir={self.weights_dir}",
            "model_loaded": self._model is not None,
            "use_gpu": self.use_gpu,
            "weights_dir": str(self.weights_dir),
            "upstream_root": str(self.upstream_root),
        }


def contract_result_to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)
