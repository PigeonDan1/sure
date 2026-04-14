"""Stable exports for the ffmpeg wrapper package."""

from .model import (
    AudioProcessResult,
    FFmpegWrapper,
    InferenceError,
    ModelLoadError,
    ModelWrapper,
)

__all__ = [
    "ModelWrapper",
    "FFmpegWrapper",
    "AudioProcessResult",
    "ModelLoadError",
    "InferenceError",
]
