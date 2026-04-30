from __future__ import annotations

import contextlib
import os
import sys
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
    timestamps: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(
        self,
        model_path: str | None = None,
        device: str = "auto",
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = config or {}
        self.model_id = model_path or self.config.get("model_id") or os.environ.get("MODEL_ID", "nvidia/parakeet-rnnt-1.1b")
        self.device = device or self.config.get("device") or os.environ.get("DEVICE", "cpu")
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
            with contextlib.redirect_stdout(sys.stderr):
                import nemo.collections.asr as nemo_asr
                import torch

                model = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained(
                    model_name=self.model_id,
                )
                if self.device == "cuda" and torch.cuda.is_available():
                    model = model.to("cuda")
                self._model = model
        except ModuleNotFoundError as exc:
            raise ModelLoadError(
                f"Failed to load {self.model_id}: missing dependency '{exc.name}' in the configured model runtime"
            ) from exc
        except Exception as exc:
            raise ModelLoadError(f"Failed to load {self.model_id}: {exc}") from exc

    def predict(self, input_data: str | Path) -> TranscriptionResult:
        if self._model is None:
            self.load()
        audio_path = Path(input_data).expanduser().resolve()
        if not audio_path.exists():
            raise InferenceError(f"Audio file not found: {audio_path}")
        try:
            with contextlib.redirect_stdout(sys.stderr):
                outputs = self._model.transcribe([str(audio_path)], timestamps=False)
            if not outputs:
                raise InferenceError("transcribe returned no outputs")
            first = outputs[0]
            text = self._extract_text(first)
            return TranscriptionResult(text=text, language="en", timestamps=[])
        except Exception as exc:
            if isinstance(exc, InferenceError):
                raise
            raise InferenceError(f"Inference failed for {self.model_id}: {exc}") from exc

    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None = None,
        return_timestamps: bool = False,
    ) -> TranscriptionResult:
        del language
        if self._model is None:
            self.load()
        resolved_audio = Path(audio_path).expanduser().resolve()
        if not resolved_audio.exists():
            raise InferenceError(f"Audio file not found: {resolved_audio}")
        try:
            with contextlib.redirect_stdout(sys.stderr):
                outputs = self._model.transcribe(
                    [str(resolved_audio)],
                    timestamps=return_timestamps,
                )
            if not outputs:
                raise InferenceError("transcribe returned no outputs")
            first = outputs[0]
            timestamps = self._extract_timestamps(first) if return_timestamps else []
            return TranscriptionResult(
                text=self._extract_text(first),
                language="en",
                timestamps=timestamps,
            )
        except Exception as exc:
            if isinstance(exc, InferenceError):
                raise
            raise InferenceError(f"Inference failed for {self.model_id}: {exc}") from exc

    def _extract_text(self, output: Any) -> str:
        if hasattr(output, "text") and isinstance(output.text, str):
            return output.text
        if isinstance(output, dict):
            for field in ("text", "transcript", "transcription", "result"):
                value = output.get(field)
                if isinstance(value, str) and value.strip():
                    return value
        text = str(output).strip()
        if not text:
            raise InferenceError("transcribe returned an empty transcription")
        return text

    def _extract_timestamps(self, output: Any) -> list[dict[str, Any]]:
        timestamp_data = getattr(output, "timestamp", None)
        if not timestamp_data:
            return []

        if isinstance(timestamp_data, dict):
            segments = timestamp_data.get("segment") or []
            if segments:
                return [
                    {
                        "start": segment.get("start", 0),
                        "end": segment.get("end", 0),
                        "text": segment.get("segment", "") or segment.get("text", ""),
                    }
                    for segment in segments
                ]
            words = timestamp_data.get("word") or []
            return [
                {
                    "start": word.get("start", 0),
                    "end": word.get("end", 0),
                    "text": word.get("word", "") or word.get("text", ""),
                }
                for word in words
            ]

        return []

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": "Model loaded" if self._model is not None else "Model not loaded",
            "model_loaded": self._model is not None,
            "model_id": self.model_id,
            "device": self.device,
            "tmpdir": self.tmpdir,
        }
