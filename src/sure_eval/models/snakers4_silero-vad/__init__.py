"""
Silero VAD Model Package for SURE-EVAL.

This package provides a unified interface for the Silero VAD model.

Exports:
    VADModel: Main wrapper class for VAD inference
    VADResult: Result type containing speech segments
    predict_vad: Convenience function for direct usage

Example:
    from model import VADModel, VADResult
    
    model = VADModel(device='cpu')
    result = model.predict("audio.wav")
    print(result.speech_segments)
"""

from .model import VADModel, VADResult, predict_vad

__all__ = ["VADModel", "VADResult", "predict_vad"]
