"""
ASR Parakeet Model for SURE-EVAL.

NVIDIA Parakeet-TDT-0.6B-v2 ASR model.
"""

from __future__ import annotations

from .model import ASRParakeetModel, TranscriptionResult

__all__ = ["ASRParakeetModel", "TranscriptionResult"]
