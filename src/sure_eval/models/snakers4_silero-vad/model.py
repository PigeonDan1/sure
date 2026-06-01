"""
Silero VAD Wrapper for SURE-EVAL.

Responsibilities:
- Load Silero VAD model from pip package
- Provide VAD inference interface
- Manage device placement (CPU/GPU)

Entry Points:
- VADModel: Main wrapper class
- VADResult: Output type with speech segments

Dependencies:
- torch>=2.0
- torchaudio>=2.0
- silero-vad>=6.2.1

Example:
    model = VADModel(device='cpu')
    result = model.predict("audio.wav")
    print(result.speech_segments)
"""

import json
import os
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
import torch


@dataclass
class VADResult:
    """VAD inference result.
    
    Attributes:
        speech_segments: List of speech segments with 'start' and 'end' times in seconds
        audio_duration: Total duration of the audio in seconds (optional)
    """
    speech_segments: List[Dict[str, float]]
    audio_duration: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())


class VADModel:
    """Silero VAD Model Wrapper for SURE-EVAL.
    
    This wrapper provides a unified interface for the Silero VAD model,
    handling model loading, audio preprocessing, and inference.
    
    Attributes:
        device: Device to run inference on ('cpu' or 'cuda')
        model: The underlying Silero VAD model
        _loaded: Whether the model has been loaded
    """
    
    def __init__(self, device: str = 'cpu', config: Optional[Dict[str, Any]] = None):
        """Initialize the VAD wrapper.
        
        Args:
            device: Device to run inference on ('cpu' or 'cuda')
            config: Optional configuration dictionary
        """
        self.device = device
        self.config = config or {}
        self.model = None
        self._loaded = False
        
        # Set torch threads for CPU inference
        if device == 'cpu':
            torch.set_num_threads(1)
    
    def load(self) -> None:
        """Load the Silero VAD model.
        
        This method loads the model weights into memory. It is called
        automatically on first predict() if not called explicitly.
        
        Raises:
            RuntimeError: If model loading fails
        """
        if self._loaded:
            return
        
        try:
            from silero_vad import load_silero_vad
            self.model = load_silero_vad()
            self._loaded = True
        except Exception as e:
            raise RuntimeError(f"Failed to load Silero VAD model: {e}")
    
    def predict(self, audio_path: str, sampling_rate: int = 16000) -> VADResult:
        """Run VAD inference on an audio file.
        
        Args:
            audio_path: Path to the audio file
            sampling_rate: Target sampling rate (default: 16000 for Silero VAD)
        
        Returns:
            VADResult containing speech segments
        
        Raises:
            RuntimeError: If inference fails
            FileNotFoundError: If audio file doesn't exist
        """
        if not self._loaded:
            self.load()
        
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        try:
            from silero_vad import read_audio, get_speech_timestamps
            
            # Load audio
            audio = read_audio(audio_path, sampling_rate=sampling_rate)
            
            # Calculate audio duration
            audio_duration = len(audio) / sampling_rate
            
            # Run VAD
            speech_timestamps = get_speech_timestamps(
                audio, 
                self.model, 
                return_seconds=True
            )
            
            return VADResult(
                speech_segments=speech_timestamps,
                audio_duration=audio_duration
            )
            
        except Exception as e:
            raise RuntimeError(f"VAD inference failed: {e}")
    
    def healthcheck(self) -> Dict[str, Any]:
        """Check if the model is ready for inference.
        
        Returns:
            Dictionary with status information
        """
        status = {
            "status": "ready" if self._loaded else "loading",
            "model_loaded": self._loaded,
            "device": self.device,
            "message": "Model is ready" if self._loaded else "Model not loaded yet"
        }
        
        if self._loaded and self.model is not None:
            try:
                # Try a dummy inference to verify model works
                import torch
                dummy_input = torch.zeros(16000)  # 1 second of silence
                from silero_vad import get_speech_timestamps
                _ = get_speech_timestamps(dummy_input, self.model, return_seconds=True)
                status["inference_test"] = "passed"
            except Exception as e:
                status["status"] = "error"
                status["message"] = f"Inference test failed: {e}"
                status["inference_test"] = "failed"
        
        return status


# Convenience function for direct usage
def predict_vad(audio_path: str, device: str = 'cpu') -> VADResult:
    """Convenience function to run VAD on an audio file.
    
    Args:
        audio_path: Path to the audio file
        device: Device to run inference on
    
    Returns:
        VADResult containing speech segments
    """
    model = VADModel(device=device)
    return model.predict(audio_path)
