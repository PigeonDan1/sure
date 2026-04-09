"""
ASR Qwen3 Model Wrapper.

Simple wrapper for Qwen3-ASR-1.7B model.
"""

from __future__ import annotations

import importlib.machinery
import os
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

TARGET_SAMPLE_RATE = 16000
DEFAULT_HF_CACHE_ROOT = Path("/root/.cache/huggingface/hub")


@dataclass
class TranscriptionResult:
    """Result of transcription."""
    text: str
    language: str | None = None
    timestamps: list[dict] | None = None


class ASRQwen3Model:
    """Wrapper for Qwen3-ASR-1.7B model."""
    
    def __init__(
        self,
        model_path: str | None = None,
        device: str = "auto",
        use_aligner: bool = False,
    ):
        """
        Initialize ASR model.
        
        Args:
            model_path: Path to model (HF repo or local)
            device: Device to use (auto, cuda, cpu)
            use_aligner: Whether to load forced aligner
        """
        self.model_path = model_path or os.environ.get(
            "MODEL_PATH", "Qwen/Qwen3-ASR-1.7B"
        )
        self.device = device
        self.use_aligner = use_aligner
        
        self._model = None
        self._processor = None

    def _ensure_torchvision_compat(self) -> None:
        """Stub torchvision when the host install is incompatible with torch."""
        try:
            import torchvision  # noqa: F401
            return
        except Exception:
            pass

        def _stub_module(name: str) -> types.ModuleType:
            module = types.ModuleType(name)
            module.__spec__ = importlib.machinery.ModuleSpec(
                name, loader=None, is_package=True
            )
            module.__path__ = []
            return module

        torchvision = _stub_module("torchvision")
        transforms = _stub_module("torchvision.transforms")
        transforms_v2 = _stub_module("torchvision.transforms.v2")
        transforms_v2_functional = _stub_module(
            "torchvision.transforms.v2.functional"
        )
        io = _stub_module("torchvision.io")

        class _InterpolationMode:
            NEAREST = "nearest"
            NEAREST_EXACT = "nearest-exact"
            BILINEAR = "bilinear"
            BICUBIC = "bicubic"
            LANCZOS = "lanczos"
            HAMMING = "hamming"
            BOX = "box"

        transforms.InterpolationMode = _InterpolationMode
        transforms.v2 = transforms_v2
        transforms_v2.functional = transforms_v2_functional
        torchvision.transforms = transforms
        torchvision.io = io

        sys.modules["torchvision"] = torchvision
        sys.modules["torchvision.transforms"] = transforms
        sys.modules["torchvision.transforms.v2"] = transforms_v2
        sys.modules["torchvision.transforms.v2.functional"] = (
            transforms_v2_functional
        )
        sys.modules["torchvision.io"] = io

    def _normalize_audio(self, audio_path: str | Path) -> tuple[np.ndarray, int]:
        """Load audio with soundfile and normalize it to mono 16k float32."""
        audio, sample_rate = sf.read(
            str(audio_path), dtype="float32", always_2d=False
        )
        audio = np.asarray(audio, dtype=np.float32)

        if audio.ndim == 2:
            # soundfile usually returns (num_frames, num_channels)
            audio = audio.mean(axis=1, dtype=np.float32)
        elif audio.ndim != 1:
            raise ValueError(f"Unsupported audio shape: {audio.shape}")

        sample_rate = int(sample_rate)
        if sample_rate != TARGET_SAMPLE_RATE:
            try:
                from scipy.signal import resample_poly
            except ImportError as exc:
                raise RuntimeError(
                    "scipy is required to resample non-16k audio for qwen3_asr"
                ) from exc

            gcd = np.gcd(sample_rate, TARGET_SAMPLE_RATE)
            up = TARGET_SAMPLE_RATE // gcd
            down = sample_rate // gcd
            audio = resample_poly(audio, up, down).astype(np.float32)
            sample_rate = TARGET_SAMPLE_RATE

        return audio, sample_rate

    def _resolve_model_path(self) -> str:
        """Prefer a local Hugging Face cache snapshot when available."""
        candidate = Path(self.model_path)
        if candidate.exists():
            return str(candidate)

        if "/" not in self.model_path:
            return self.model_path

        cache_root = Path(
            os.environ.get("HF_HUB_CACHE", DEFAULT_HF_CACHE_ROOT)
        )
        repo_dir = cache_root / f"models--{self.model_path.replace('/', '--')}"
        snapshots_dir = repo_dir / "snapshots"
        if not snapshots_dir.exists():
            return self.model_path

        snapshots = sorted(
            (path for path in snapshots_dir.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if snapshots:
            return str(snapshots[0])

        return self.model_path
    
    def _load_model(self) -> None:
        """Lazy load the model."""
        if self._model is not None:
            return

        self._ensure_torchvision_compat()

        try:
            from qwen_asr import Qwen3ASRModel
            import torch
        except ImportError:
            raise RuntimeError(
                "qwen-asr not installed. Run: pip install qwen-asr"
            )
        
        # Determine device
        if self.device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            device = self.device
        
        dtype = torch.bfloat16 if device == "cuda" else torch.float32
        resolved_model_path = self._resolve_model_path()
        
        # Load model
        self._model = Qwen3ASRModel.from_pretrained(
            resolved_model_path,
            dtype=dtype,
            device_map=device if device != "cpu" else None,
        )
    
    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None = None,
        return_timestamps: bool = False,
    ) -> TranscriptionResult:
        """
        Transcribe audio file.
        
        Args:
            audio_path: Path to audio file
            language: Language code (e.g., "Chinese", "English")
            return_timestamps: Whether to return timestamps
            
        Returns:
            TranscriptionResult with text and optional timestamps
        """
        self._load_model()

        # qwen_asr expects language names like "Chinese" rather than "zh".
        normalized_language = None
        if language and language != "auto":
            normalized_language = language

        audio_input = self._normalize_audio(audio_path)

        results = self._model.transcribe(
            audio_input,
            language=normalized_language,
            return_time_stamps=return_timestamps,
        )
        
        if not results:
            return TranscriptionResult(text="")
        
        result = results[0]
        text = result.text
        
        # Extract timestamps if available
        timestamps = None
        if return_timestamps and hasattr(result, 'time_stamps'):
            timestamps = [
                {"start": ts.start, "end": ts.end, "text": ts.text}
                for ts in result.time_stamps
            ]
        
        return TranscriptionResult(
            text=text,
            language=language,
            timestamps=timestamps,
        )
    
    def transcribe_batch(
        self,
        audio_paths: list[str | Path],
        language: str | None = None,
    ) -> list[TranscriptionResult]:
        """
        Transcribe multiple audio files.
        
        Args:
            audio_paths: List of audio file paths
            language: Language code
            
        Returns:
            List of TranscriptionResult
        """
        return [
            self.transcribe(path, language)
            for path in audio_paths
        ]
