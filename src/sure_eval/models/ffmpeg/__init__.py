"""
FFmpeg Wrapper Package for SURE-EVAL.

Exports:
    FFmpegWrapper: Main wrapper class for FFmpeg audio processing
    AudioProcessResult: Result type for audio processing operations
    MCPServer: MCP server for FFmpeg integration

Example:
    from ffmpeg import FFmpegWrapper
    
    wrapper = FFmpegWrapper()
    result = wrapper.predict(
        input_path="input.wav",
        output_path="output.wav",
        start_time=0,
        duration=3,
        sample_rate=16000,
        channels=1
    )
    print(result.output_path)
"""

from .model import FFmpegWrapper, AudioProcessResult, ModelLoadError, InferenceError

__all__ = [
    "FFmpegWrapper",
    "AudioProcessResult", 
    "ModelLoadError",
    "InferenceError"
]

# MCP server is available but not auto-imported to avoid heavy dependencies
# Use: from ffmpeg.server import MCPServer
