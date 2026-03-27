"""
ASR Parakeet Model Wrapper.

Simple wrapper for NVIDIA Parakeet-TDT-0.6B-v2 model.
Uses NVIDIA NeMo toolkit for inference.

Model: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2
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


class ASRParakeetModel:
    """Wrapper for NVIDIA Parakeet-TDT-0.6B-v2 model."""
    
    MODEL_ID = "nvidia/parakeet-tdt-0.6b-v2"
    
    def __init__(
        self,
        model_path: str | None = None,
        device: str = "auto",
    ):
        """
        Initialize ASR model.
        
        Args:
            model_path: Path to model (HF repo or local). 
                       Defaults to "nvidia/parakeet-tdt-0.6b-v2"
            device: Device to use (auto, cuda, cpu)
        """
        self.model_path = model_path or os.environ.get(
            "MODEL_PATH", self.MODEL_ID
        )
        self.device = device
        
        self._model = None
    
    def _load_model(self) -> None:
        """Lazy load the model."""
        if self._model is not None:
            return
        
        try:
            import nemo.collections.asr as nemo_asr
            import torch
        except ImportError as e:
            raise RuntimeError(
                f"Required package not installed: {e}. "
                "Run: pip install nemo-toolkit[asr] torch"
            )
        
        # Determine device
        if self.device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            device = self.device
        
        print(f"Loading Parakeet model: {self.model_path}")
        print(f"Device: {device}")
        
        # Load model using NeMo
        self._model = nemo_asr.models.ASRModel.from_pretrained(
            model_name=self.model_path,
        )
        
        # Move to device
        if device == "cuda" and torch.cuda.is_available():
            self._model = self._model.to("cuda")
            # Use bfloat16 for better performance on modern GPUs
            if torch.cuda.is_bf16_supported():
                self._model = self._model.to(torch.bfloat16)
        
        print("Model loaded successfully")
    
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
            language: Language code (e.g., "en"). 
                     Note: Parakeet only supports English.
            return_timestamps: Whether to return timestamps
            
        Returns:
            TranscriptionResult with text and optional timestamps
        """
        self._load_model()
        
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        # Transcribe using NeMo
        # Output is a list of Hypothesis objects
        outputs = self._model.transcribe(
            [str(audio_path)],
            timestamps=return_timestamps,
        )
        
        if not outputs:
            return TranscriptionResult(text="")
        
        # Extract result
        output = outputs[0]
        text = output.text if hasattr(output, 'text') else str(output)
        
        # Extract timestamps if available
        timestamps = None
        if return_timestamps and hasattr(output, 'timestamp') and output.timestamp:
            ts_data = output.timestamp
            # NeMo provides word, segment, char level timestamps
            timestamps = []
            
            # Segment level timestamps
            if 'segment' in ts_data:
                for seg in ts_data['segment']:
                    timestamps.append({
                        "start": seg.get('start', 0),
                        "end": seg.get('end', 0),
                        "text": seg.get('segment', ''),
                        "level": "segment",
                    })
            # Word level timestamps
            elif 'word' in ts_data:
                for word in ts_data['word']:
                    timestamps.append({
                        "start": word.get('start', 0),
                        "end": word.get('end', 0),
                        "text": word.get('word', ''),
                        "level": "word",
                    })
        
        return TranscriptionResult(
            text=text,
            language="en",  # Parakeet only supports English
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
