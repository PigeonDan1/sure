from __future__ import annotations

import importlib.machinery
import os
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

MODEL_DIR = Path(__file__).resolve().parent
TARGET_SAMPLE_RATE = 16000


def _stub_torchvision_when_incompatible() -> None:
    try:
        import torchvision  # noqa: F401
        return
    except Exception:
        pass

    def package(name: str) -> types.ModuleType:
        mod = types.ModuleType(name)
        mod.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
        mod.__path__ = []
        return mod

    torchvision = package("torchvision")
    transforms = package("torchvision.transforms")
    transforms_v2 = package("torchvision.transforms.v2")
    transforms_v2_functional = package("torchvision.transforms.v2.functional")
    io = package("torchvision.io")

    class InterpolationMode:
        NEAREST = "nearest"
        NEAREST_EXACT = "nearest-exact"
        BILINEAR = "bilinear"
        BICUBIC = "bicubic"
        LANCZOS = "lanczos"
        HAMMING = "hamming"
        BOX = "box"

    transforms.InterpolationMode = InterpolationMode
    transforms.v2 = transforms_v2
    transforms_v2.functional = transforms_v2_functional
    torchvision.transforms = transforms
    torchvision.io = io

    sys.modules.update(
        {
            "torchvision": torchvision,
            "torchvision.transforms": transforms,
            "torchvision.transforms.v2": transforms_v2,
            "torchvision.transforms.v2.functional": transforms_v2_functional,
            "torchvision.io": io,
        }
    )


def _read_audio_16k(audio_path: str | Path) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=False)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1, dtype=np.float32)
    if audio.ndim != 1:
        raise ValueError(f"Unsupported audio shape: {audio.shape}")

    sample_rate = int(sample_rate)
    if sample_rate == TARGET_SAMPLE_RATE:
        return audio, sample_rate

    from scipy.signal import resample_poly

    gcd = int(np.gcd(sample_rate, TARGET_SAMPLE_RATE))
    audio = resample_poly(audio, TARGET_SAMPLE_RATE // gcd, sample_rate // gcd)
    return audio.astype(np.float32), TARGET_SAMPLE_RATE


class ModelWrapper:
    def __init__(self, model_root: str | Path | None = None, device: str | None = None):
        self.model_root = Path(model_root or MODEL_DIR).resolve()
        self.device = device or os.environ.get("DEVICE", "cuda")
        self.model_path = os.environ.get(
            "MODEL_PATH",
            str(self.model_root / ".runtime" / "modelscope_cache" / "models" / "Qwen" / "Qwen3-ASR-1___7B"),
        )
        self._model = None

    def health(self) -> dict[str, Any]:
        import torch

        return {
            "loaded": self._model is not None,
            "device": self.device,
            "cuda_available": bool(torch.cuda.is_available()),
            "model_path": self.model_path,
        }

    def load(self) -> None:
        if self._model is not None:
            return
        _stub_torchvision_when_incompatible()

        import torch
        from qwen_asr import Qwen3ASRModel

        if self.device == "auto":
            resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            resolved_device = self.device

        if resolved_device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("Qwen3-ASR requires GPU validation, but torch.cuda.is_available() is false.")

        dtype = torch.float16 if resolved_device == "cuda" else torch.float32
        device_map = resolved_device if resolved_device != "cpu" else None
        self._model = Qwen3ASRModel.from_pretrained(self.model_path, dtype=dtype, device_map=device_map)
        self.device = resolved_device

    def predict(self, request: dict[str, Any]) -> dict[str, Any]:
        audio_path = request.get("audio_path")
        if not audio_path:
            raise ValueError("request.audio_path is required")
        language = request.get("language")
        if language == "auto":
            language = None

        self.load()
        audio = _read_audio_16k(audio_path)
        results = self._model.transcribe(audio, language=language, return_time_stamps=False)
        text = ""
        if results:
            text = str(getattr(results[0], "text", "")).strip()
        return {"text": text, "language": language, "raw": {"num_results": len(results or [])}}


ASRQwen3ReonboardModel = ModelWrapper
