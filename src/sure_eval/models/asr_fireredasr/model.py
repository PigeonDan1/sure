"""
FireRedASR-LLM-L Model Wrapper for SURE-EVAL.

Wrapper for FireRedTeam/FireRedASR-LLM-L (8.3B params, encoder-adapter-LLM).
Supports CPU inference by default; GPU is attempted when available and
sufficient VRAM is free, with automatic fallback to CPU on OOM.
"""

from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure fireredasr source is importable
_MODEL_ROOT = Path(__file__).resolve().parent
_FIRERED_SRC = str(_MODEL_ROOT / "fireredasr")
if _FIRERED_SRC not in sys.path:
    sys.path.insert(0, _FIRERED_SRC)


class ModelLoadError(RuntimeError):
    """Raised when the model fails to load."""


class InferenceError(RuntimeError):
    """Raised when inference fails."""


@dataclass
class TranscriptionResult:
    """Result of transcription."""
    text: str
    language: str | None = None
    timestamps: list[dict] | None = None


class ModelWrapper:
    """Wrapper for FireRedASR-LLM-L."""

    def __init__(
        self,
        model_path: str | None = None,
        device: str = "auto",
    ):
        """
        Initialize the model wrapper.

        Args:
            model_path: Path to the model directory. If None, uses environment
                variable FIREREDASR_MODEL_PATH or the default local checkpoint.
            device: "auto", "cuda", or "cpu". Auto prefers GPU but falls back
                to CPU if CUDA OOM occurs during inference.
        """
        self.model_path = model_path or os.environ.get(
            "FIREREDASR_MODEL_PATH",
            str(_MODEL_ROOT / "checkpoints" / "pretrained_models" / "fireredasr_llm_l"),
        )
        self.device = device
        self._model = None
        self._use_gpu = False  # determined at load/infer time

    def _resolve_model_path(self) -> str:
        """Resolve model path, preferring local checkpoint."""
        candidate = Path(self.model_path)
        if candidate.exists():
            return str(candidate)
        return self.model_path

    def load(self) -> None:
        """Eagerly load the model."""
        if self._model is not None:
            return

        try:
            from fireredasr.models.fireredasr import FireRedAsr
        except ImportError as exc:
            raise ModelLoadError(
                "Cannot import FireRedAsr. Is the fireredasr source present?"
            ) from exc

        resolved = self._resolve_model_path()
        if not Path(resolved).exists():
            raise ModelLoadError(f"Model directory not found: {resolved}")

        # Determine GPU vs CPU
        self._use_gpu = False
        if self.device == "auto":
            try:
                import torch
                self._use_gpu = torch.cuda.is_available()
            except Exception:
                self._use_gpu = False
        elif self.device == "cuda":
            self._use_gpu = True
        else:
            self._use_gpu = False

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            self._model = FireRedAsr.from_pretrained("llm", resolved)

    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None = None,
        return_timestamps: bool = False,
    ) -> TranscriptionResult:
        """
        Transcribe a single audio file.

        Args:
            audio_path: Path to the audio file (wav recommended, 16kHz mono).
            language: Optional language hint (currently unused by FireRedASR-LLM).
            return_timestamps: Whether to return timestamps (not supported).

        Returns:
            TranscriptionResult with transcribed text.
        """
        self.load()

        if self._model is None:
            raise InferenceError("Model is not loaded.")

        # FireRedASR expects lists
        uttid = [Path(audio_path).stem]
        wav_path = [str(audio_path)]

        args = {
            "use_gpu": 1 if self._use_gpu else 0,
            "beam_size": 1,
            "decode_max_len": 0,
            "decode_min_len": 0,
            "repetition_penalty": 3.0,
            "llm_length_penalty": 1.0,
            "temperature": 1.0,
        }

        try:
            results = self._model.transcribe(uttid, wav_path, args)
        except Exception as exc:
            # If CUDA OOM, try CPU fallback once
            err_msg = str(exc).lower()
            if "out of memory" in err_msg and self._use_gpu:
                self._use_gpu = False
                args["use_gpu"] = 0
                results = self._model.transcribe(uttid, wav_path, args)
            else:
                raise InferenceError(f"Transcription failed: {exc}") from exc

        if not results:
            return TranscriptionResult(text="")

        text = results[0].get("text", "")
        return TranscriptionResult(text=text, language=language)

    def transcribe_batch(
        self,
        audio_paths: list[str | Path],
        language: str | None = None,
    ) -> list[TranscriptionResult]:
        """Transcribe multiple audio files."""
        return [
            self.transcribe(path, language=language)
            for path in audio_paths
        ]
