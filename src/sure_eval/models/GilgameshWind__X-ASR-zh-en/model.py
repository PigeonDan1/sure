from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


MODEL_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_ROOT = MODEL_DIR / ".runtime/huggingface/GilgameshWind/X-ASR-zh-en"

_CJK_RANGE = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_PUNCT = re.escape("，。！？；：、（）《》〈〉【】「」『』“”‘’")
_ASCII_PUNCT_NO_LEADING_SPACE = re.escape(",.!?;:%)]}")


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass
class TranscriptionResult:
    text: str
    language: str | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.model_id = self.config.get("model_id") or "GilgameshWind/X-ASR-zh-en"
        self.model_root = Path(
            self.config.get("model_root")
            or os.environ.get("X_ASR_MODEL_ROOT")
            or DEFAULT_MODEL_ROOT
        ).resolve()
        self.chunk = str(self.config.get("chunk") or os.environ.get("X_ASR_CHUNK") or "960")
        self.provider = self.config.get("provider") or os.environ.get("SHERPA_ONNX_PROVIDER") or "cpu"
        self.num_threads = int(self.config.get("num_threads") or os.environ.get("SHERPA_ONNX_NUM_THREADS") or 1)
        self.sample_rate = int(self.config.get("sample_rate") or 16000)
        self.feature_dim = int(self.config.get("feature_dim") or 80)
        self.tail_padding_seconds = float(
            self.config.get("tail_padding_seconds")
            or os.environ.get("X_ASR_TAIL_PADDING_SECONDS")
            or 1.0
        )
        self._recognizer = None

    def _variant_dir(self) -> Path:
        return self.model_root / "deployment/models" / f"chunk-{self.chunk}ms-model"

    def _paths(self) -> dict[str, Path]:
        variant = self._variant_dir()
        return {
            "tokens": variant / "tokens.txt",
            "encoder": variant / f"encoder-{self.chunk}ms.onnx",
            "decoder": variant / f"decoder-{self.chunk}ms.onnx",
            "joiner": variant / f"joiner-{self.chunk}ms.onnx",
        }

    def load(self) -> None:
        if self._recognizer is not None:
            return
        paths = self._paths()
        missing = [str(path) for path in paths.values() if not path.exists()]
        if missing:
            raise ModelLoadError("Missing X-ASR model files: " + ", ".join(missing))
        try:
            import sherpa_onnx

            self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(paths["tokens"]),
                encoder=str(paths["encoder"]),
                decoder=str(paths["decoder"]),
                joiner=str(paths["joiner"]),
                num_threads=self.num_threads,
                sample_rate=self.sample_rate,
                feature_dim=self.feature_dim,
                decoding_method="greedy_search",
                provider=self.provider,
                model_type="zipformer2",
                enable_endpoint_detection=False,
            )
        except Exception as exc:
            raise ModelLoadError(f"Failed to load {self.model_id}: {exc}") from exc

    def predict(self, input_data: str | dict[str, Any]) -> TranscriptionResult:
        payload = input_data if isinstance(input_data, dict) else {"audio_path": input_data}
        audio_path = payload.get("audio_path") or payload.get("path") or payload.get("input")
        if not audio_path:
            raise ValueError("audio_path is required")
        if self._recognizer is None:
            self.load()
        assert self._recognizer is not None
        try:
            samples, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=False)
            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            if sample_rate != self.sample_rate:
                import librosa

                samples = librosa.resample(samples.astype(np.float32), orig_sr=sample_rate, target_sr=self.sample_rate)
                sample_rate = self.sample_rate
            stream = self._recognizer.create_stream()
            waveform = samples.astype(np.float32).reshape(-1)
            if self.tail_padding_seconds > 0:
                tail_samples = int(sample_rate * self.tail_padding_seconds)
                waveform = np.concatenate([waveform, np.zeros(tail_samples, dtype=np.float32)])
            stream.accept_waveform(sample_rate, waveform)
            stream.input_finished()
            while self._recognizer.is_ready(stream):
                self._recognizer.decode_stream(stream)
            text = _normalize_text(self._recognizer.get_result(stream))
            return TranscriptionResult(
                text=text,
                raw={
                    "model_id": self.model_id,
                    "chunk_ms": self.chunk,
                    "provider": self.provider,
                    "sample_rate": sample_rate,
                    "tail_padding_seconds": self.tail_padding_seconds,
                    "audio_path": str(audio_path),
                },
            )
        except Exception as exc:
            raise InferenceError(f"Inference failed for {self.model_id}: {exc}") from exc

    def healthcheck(self) -> dict[str, Any]:
        paths = self._paths()
        return {
            "status": "loaded" if self._recognizer is not None else "ready",
            "model_loaded": self._recognizer is not None,
            "model_id": self.model_id,
            "model_root": str(self.model_root),
            "chunk_ms": self.chunk,
            "provider": self.provider,
            "tail_padding_seconds": self.tail_padding_seconds,
            "required_files_present": all(path.exists() for path in paths.values()),
        }


def _normalize_text(text: str) -> str:
    text = re.sub(rf"(?<=[{_CJK_RANGE}])\s+(?=[{_CJK_RANGE}])", "", text)
    text = re.sub(rf"(?<=[{_CJK_RANGE}])\s+(?=[{_CJK_PUNCT}])", "", text)
    text = re.sub(rf"(?<=[{_CJK_PUNCT}])\s+(?=[{_CJK_RANGE}])", "", text)
    text = re.sub(rf"(?<=[{_CJK_PUNCT}])\s+(?=[{_CJK_PUNCT}])", "", text)
    text = re.sub(rf"\s+(?=[{_ASCII_PUNCT_NO_LEADING_SPACE}])", "", text)
    return text.strip()
