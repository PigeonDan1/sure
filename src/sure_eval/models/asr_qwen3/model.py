"""
ASR Qwen3 Model Wrapper.

Simple wrapper for Qwen3-ASR-1.7B model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    
    def _load_model(self) -> None:
        """Lazy load the model."""
        if self._model is not None:
            return
        
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
        
        # Load model
        self._model = Qwen3ASRModel.from_pretrained(
            self.model_path,
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
        
        # Map language names to codes if needed
        lang_code = None
        if language and language != "auto":
            lang_map = {
                "chinese": "zh",
                "english": "en",
                "japanese": "ja",
                "korean": "ko",
            }
            lang_code = lang_map.get(language.lower(), language)
        
        # Transcribe
        results = self._model.transcribe(
            str(audio_path),
            language=lang_code,
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
