"""
ASR Whisper Model Wrapper.

Simple wrapper for OpenAI Whisper model.
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


class ASRWhisperModel:
    """Wrapper for OpenAI Whisper model."""
    
    def __init__(
        self,
        model_path: str | None = None,
        device: str = "auto",
    ):
        """
        Initialize ASR model.
        
        Args:
            model_path: Model size or path (tiny, base, small, medium, large, large-v3)
            device: Device to use (auto, cuda, cpu)
        """
        self.model_path = model_path or os.environ.get(
            "MODEL_PATH", "large-v3"
        )
        self.device = device
        
        self._model = None
    
    def _load_model(self) -> None:
        """Lazy load the model."""
        if self._model is not None:
            return
        
        try:
            import whisper
        except ImportError:
            raise RuntimeError(
                "openai-whisper not installed. Run: pip install openai-whisper"
            )
        
        # Determine device
        if self.device == "auto":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            device = self.device
        
        # Load model - whisper will auto-download if needed
        print(f"Loading Whisper model: {self.model_path}")
        self._model = whisper.load_model(self.model_path, device=device)
        print(f"Model loaded on {device}")
    
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
            language: Language code (e.g., "zh", "en")
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
                "french": "fr",
                "german": "de",
                "spanish": "es",
                "italian": "it",
                "portuguese": "pt",
                "russian": "ru",
            }
            lang_code = lang_map.get(language.lower(), language)
        
        # Prepare options
        options = {}
        if lang_code:
            options["language"] = lang_code
        
        # Transcribe
        result = self._model.transcribe(str(audio_path), **options)
        
        text = result.get("text", "").strip()
        detected_language = result.get("language")
        
        # Extract timestamps if available and requested
        timestamps = None
        if return_timestamps:
            segments = result.get("segments", [])
            timestamps = [
                {
                    "start": seg.get("start", 0),
                    "end": seg.get("end", 0),
                    "text": seg.get("text", "").strip(),
                }
                for seg in segments
            ]
        
        return TranscriptionResult(
            text=text,
            language=detected_language or lang_code,
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
