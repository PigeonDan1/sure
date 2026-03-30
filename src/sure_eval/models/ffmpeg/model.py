"""
FFmpeg Wrapper for SURE-EVAL.

Responsibilities:
- Provide audio processing interface via FFmpeg system calls
- Handle audio format conversion, clipping, and extraction
- Manage output file paths

Entry Points:
- FFmpegWrapper: Main wrapper class
- AudioProcessResult: Output type

Dependencies:
- ffmpeg (system binary)
- ffprobe (system binary)

Example:
    wrapper = FFmpegWrapper()
    result = wrapper.predict("input.wav", output_path="output.wav", 
                            start_time=0, duration=3)
    print(result.output_path)
"""

import os
import subprocess
import json
from dataclasses import dataclass
from typing import Optional, Dict, Any


class ModelLoadError(RuntimeError):
    """Raised when FFmpeg tools cannot be found."""
    pass


class InferenceError(RuntimeError):
    """Raised when audio processing fails."""
    pass


class ConfigurationError(ValueError):
    """Raised when configuration is invalid."""
    pass


@dataclass
class AudioProcessResult:
    """Result of audio processing operation.
    
    Attributes:
        output_path: Path to the processed audio file
        duration: Duration of output in seconds (if available)
        sample_rate: Sample rate of output (if available)
        channels: Number of channels (if available)
    """
    output_path: str
    duration: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    
    def __post_init__(self):
        assert isinstance(self.output_path, str)
        assert len(self.output_path) > 0, "Empty output_path"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "output_path": self.output_path,
            "duration": self.duration,
            "sample_rate": self.sample_rate,
            "channels": self.channels
        }


class FFmpegWrapper:
    """FFmpeg wrapper for SURE-EVAL.
    
    Provides a Python interface to FFmpeg audio processing capabilities.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize wrapper.
        
        Args:
            config: Optional configuration dictionary
        """
        self.config = config or {}
        self._ffmpeg_path = self.config.get('ffmpeg_path', 'ffmpeg')
        self._ffprobe_path = self.config.get('ffprobe_path', 'ffprobe')
        self._model_loaded = False
        
    def load(self) -> None:
        """Verify FFmpeg tools are available.
        
        Raises:
            ModelLoadError: If ffmpeg or ffprobe cannot be found.
        """
        try:
            subprocess.run(
                [self._ffmpeg_path, '-version'],
                capture_output=True,
                check=True
            )
            subprocess.run(
                [self._ffprobe_path, '-version'],
                capture_output=True,
                check=True
            )
            self._model_loaded = True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise ModelLoadError(f"FFmpeg tools not available: {e}")
    
    def healthcheck(self) -> Dict[str, Any]:
        """Check if FFmpeg tools are ready.
        
        Returns:
            Dict with status information
        """
        try:
            result = subprocess.run(
                [self._ffmpeg_path, '-version'],
                capture_output=True,
                check=True
            )
            version_line = result.stdout.decode().split('\n')[0]
            return {
                "status": "ready",
                "message": f"FFmpeg available: {version_line}",
                "model_loaded": self._model_loaded
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "model_loaded": False
            }
    
    def predict(
        self,
        input_path: str,
        output_path: str,
        start_time: Optional[float] = None,
        duration: Optional[float] = None,
        sample_rate: Optional[int] = None,
        channels: Optional[int] = None
    ) -> AudioProcessResult:
        """Process audio using FFmpeg.
        
        Args:
            input_path: Path to input audio file
            output_path: Path for output audio file
            start_time: Start time in seconds (for clipping)
            duration: Duration in seconds (for clipping)
            sample_rate: Target sample rate (e.g., 16000)
            channels: Target number of channels (e.g., 1 for mono)
        
        Returns:
            AudioProcessResult with output path and metadata
        
        Raises:
            InferenceError: If processing fails
        """
        if not self._model_loaded:
            self.load()
        
        if not os.path.exists(input_path):
            raise InferenceError(f"Input file not found: {input_path}")
        
        # Build FFmpeg command
        cmd = [self._ffmpeg_path, '-y', '-i', input_path]
        
        # Add time clipping if specified
        if start_time is not None:
            cmd.extend(['-ss', str(start_time)])
        if duration is not None:
            cmd.extend(['-t', str(duration)])
        
        # Add audio format options
        if sample_rate is not None:
            cmd.extend(['-ar', str(sample_rate)])
        if channels is not None:
            cmd.extend(['-ac', str(channels)])
        
        # Add output path
        cmd.append(output_path)
        
        # Execute FFmpeg
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                check=True
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "Unknown error"
            raise InferenceError(f"FFmpeg failed: {stderr}")
        
        # Verify output exists
        if not os.path.exists(output_path):
            raise InferenceError("Output file was not created")
        
        # Get output metadata using ffprobe
        duration_val = None
        sample_rate_val = None
        channels_val = None
        
        try:
            probe_cmd = [
                self._ffprobe_path,
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                output_path
            ]
            probe_result = subprocess.run(
                probe_cmd,
                capture_output=True,
                check=True
            )
            probe_data = json.loads(probe_result.stdout.decode())
            
            # Extract audio stream info
            for stream in probe_data.get('streams', []):
                if stream.get('codec_type') == 'audio':
                    sample_rate_val = int(stream.get('sample_rate', 0)) or None
                    channels_val = stream.get('channels')
                    break
            
            # Get duration from format
            format_info = probe_data.get('format', {})
            duration_str = format_info.get('duration')
            if duration_str:
                duration_val = float(duration_str)
                
        except Exception:
            # Metadata extraction is optional
            pass
        
        return AudioProcessResult(
            output_path=output_path,
            duration=duration_val,
            sample_rate=sample_rate_val,
            channels=channels_val
        )
    
    def process_audio(
        self,
        input_path: str,
        output_path: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Convenience method that returns dict instead of dataclass.
        
        Args:
            input_path: Path to input audio file
            output_path: Path for output audio file
            **kwargs: Additional options (start_time, duration, etc.)
        
        Returns:
            Dictionary with result data (JSON-serializable)
        """
        result = self.predict(input_path, output_path, **kwargs)
        return result.to_dict()
