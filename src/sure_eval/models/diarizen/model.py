"""
DiariZen Model Wrapper for Speaker Diarization.

Wrapper for DiariZen speaker diarization model based on WavLM.
Model: BUT-FIT/diarizen-wavlm-large-s80-md
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DiarizationSegment:
    """Single diarization segment."""
    start: float
    end: float
    speaker: str


@dataclass
class DiarizationResult:
    """Result of speaker diarization."""
    segments: list[DiarizationSegment] = field(default_factory=list)
    num_speakers: int = 0
    rttm: str = ""  # RTTM format output
    
    def to_rttm(self, session_name: str = "unknown") -> str:
        """Convert segments to RTTM format."""
        lines = []
        for seg in self.segments:
            line = f"SPEAKER {session_name} 1 {seg.start:.3f} {seg.end - seg.start:.3f} <NA> <NA> {seg.speaker} <NA> <NA>"
            lines.append(line)
        return "\n".join(lines)


class DiariZenModel:
    """
    Wrapper for DiariZen speaker diarization model.
    
    Model: BUT-FIT/diarizen-wavlm-large-s80-md
    Based on WavLM-Large with structured pruning.
    """
    
    def __init__(
        self,
        model_path: str | None = None,
        device: str = "auto",
        rttm_out_dir: str | None = None,
    ):
        """
        Initialize DiariZen model.
        
        Args:
            model_path: HuggingFace model ID or local path
            device: Device to use (auto, cuda, cpu)
            rttm_out_dir: Directory to save RTTM files (optional)
        """
        self.model_path = model_path or os.environ.get(
            "MODEL_PATH", "BUT-FIT/diarizen-wavlm-large-s80-md"
        )
        self.device = device
        self.rttm_out_dir = rttm_out_dir
        
        self._pipeline = None
        self._diarizen_available = False
    
    def _load_model(self) -> None:
        """Lazy load the DiariZen pipeline."""
        if self._pipeline is not None:
            return
        
        try:
            from diarizen.pipelines.inference import DiariZenPipeline
            self._diarizen_available = True
        except ImportError:
            raise RuntimeError(
                "diarizen not installed. "
                "Install from: https://github.com/BUTSpeechFIT/DiariZen"
            )
        
        # Determine device
        if self.device == "auto":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            device = self.device
        
        # Load pipeline
        print(f"Loading DiariZen model: {self.model_path}")
        self._pipeline = DiariZenPipeline.from_pretrained(
            self.model_path,
            device=device,
            rttm_out_dir=self.rttm_out_dir,
        )
        print("DiariZen model loaded")
    
    def diarize(
        self,
        audio_path: str | Path,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> DiarizationResult:
        """
        Perform speaker diarization on audio file.
        
        Args:
            audio_path: Path to audio file
            num_speakers: Exact number of speakers (optional)
            min_speakers: Minimum number of speakers (optional)
            max_speakers: Maximum number of speakers (optional)
            
        Returns:
            DiarizationResult with segments and RTTM format
        """
        self._load_model()
        
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        # Run diarization
        print(f"Diarizing: {audio_path}")
        
        # Build kwargs for speaker count constraints
        kwargs = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = num_speakers
        if min_speakers is not None:
            kwargs["min_speakers"] = min_speakers
        if max_speakers is not None:
            kwargs["max_speakers"] = max_speakers
        
        diar_results = self._pipeline(str(audio_path), **kwargs)
        
        # Parse results
        segments = []
        speakers = set()
        
        for turn, _, speaker in diar_results.itertracks(yield_label=True):
            segment = DiarizationSegment(
                start=turn.start,
                end=turn.end,
                speaker=speaker,
            )
            segments.append(segment)
            speakers.add(speaker)
        
        # Create result
        result = DiarizationResult(
            segments=segments,
            num_speakers=len(speakers),
        )
        
        # Generate RTTM
        session_name = audio_path.stem
        result.rttm = result.to_rttm(session_name)
        
        return result
    
    def diarize_with_rttm_output(
        self,
        audio_path: str | Path,
        output_dir: str | Path,
        num_speakers: int | None = None,
    ) -> DiarizationResult:
        """
        Diarize and save RTTM output to file.
        
        Args:
            audio_path: Path to audio file
            output_dir: Directory to save RTTM file
            num_speakers: Exact number of speakers (optional)
            
        Returns:
            DiarizationResult
        """
        result = self.diarize(audio_path, num_speakers=num_speakers)
        
        # Save RTTM
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        session_name = Path(audio_path).stem
        rttm_path = output_dir / f"{session_name}.rttm"
        
        with open(rttm_path, "w") as f:
            f.write(result.rttm)
        
        print(f"RTTM saved to: {rttm_path}")
        return result
