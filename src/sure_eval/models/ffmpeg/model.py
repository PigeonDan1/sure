"""Local wrapper around ffmpeg/ffprobe executables for SURE-EVAL."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


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
    """Structured output for the minimal ffmpeg callable path."""

    output_path: str
    exists: bool
    ffmpeg_exit_code: int
    ffmpeg_executable: str
    ffprobe_executable: str
    ffmpeg_version: str
    ffprobe_version: str
    ffprobe: Dict[str, Any]
    contract_passed: bool

    def __post_init__(self):
        assert isinstance(self.output_path, str)
        assert len(self.output_path) > 0, "Empty output_path"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FFmpegWrapper:
    """Thin wrapper over local ffmpeg/ffprobe executables."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._ffmpeg_path = self.config.get("ffmpeg_path", "ffmpeg")
        self._ffprobe_path = self.config.get("ffprobe_path", "ffprobe")
        self._model_loaded = False

    def _resolve_executable(self, candidate: str) -> str:
        if os.path.isabs(candidate):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            raise ModelLoadError(f"Executable is not runnable: {candidate}")
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        raise ModelLoadError(f"Executable not found on PATH: {candidate}")

    def _run_version(self, executable: str) -> tuple[str, str]:
        resolved = self._resolve_executable(executable)
        try:
            proc = subprocess.run(
                [resolved, "-version"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise ModelLoadError(f"Version probe failed for {resolved}: {exc}") from exc
        version_line = proc.stdout.splitlines()[0] if proc.stdout else ""
        return resolved, version_line

    def load(self) -> None:
        self._ffmpeg_path, _ = self._run_version(self._ffmpeg_path)
        self._ffprobe_path, _ = self._run_version(self._ffprobe_path)
        self._model_loaded = True

    def healthcheck(self) -> Dict[str, Any]:
        try:
            ffmpeg_path, ffmpeg_version = self._run_version(self._ffmpeg_path)
            ffprobe_path, ffprobe_version = self._run_version(self._ffprobe_path)
            return {
                "status": "ready",
                "message": "ffmpeg/ffprobe are available",
                "model_loaded": self._model_loaded,
                "ffmpeg_path": ffmpeg_path,
                "ffprobe_path": ffprobe_path,
                "ffmpeg_version": ffmpeg_version,
                "ffprobe_version": ffprobe_version,
            }
        except Exception as exc:
            return {
                "status": "error",
                "message": str(exc),
                "model_loaded": False,
            }

    def predict(
        self,
        input_path: str,
        output_path: str,
        sample_rate: int = 16000,
        channels: int = 1,
        codec: str = "pcm_s16le",
    ) -> AudioProcessResult:
        if not self._model_loaded:
            self.load()

        if not os.path.exists(input_path):
            raise InferenceError(f"Input file not found: {input_path}")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        ffmpeg_path, ffmpeg_version = self._run_version(self._ffmpeg_path)
        ffprobe_path, ffprobe_version = self._run_version(self._ffprobe_path)

        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            input_path,
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            codec,
        ]
        cmd.append(output_path)

        try:
            ffmpeg_proc = subprocess.run(
                cmd,
                capture_output=True,
                check=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise InferenceError(exc.stderr or "ffmpeg command failed") from exc

        exists = os.path.exists(output_path)
        if not exists:
            raise InferenceError("Output file was not created")

        try:
            probe_cmd = [
                ffprobe_path,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                output_path
            ]
            probe_proc = subprocess.run(
                probe_cmd,
                capture_output=True,
                check=True,
                text=True,
            )
            probe_data = json.loads(probe_proc.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            raise InferenceError(f"ffprobe validation failed: {exc}") from exc

        contract_passed = bool(probe_data.get("streams")) and bool(
            probe_data.get("format", {}).get("format_name")
        )
        return AudioProcessResult(
            output_path=output_path,
            exists=exists,
            ffmpeg_exit_code=ffmpeg_proc.returncode,
            ffmpeg_executable=ffmpeg_path,
            ffprobe_executable=ffprobe_path,
            ffmpeg_version=ffmpeg_version,
            ffprobe_version=ffprobe_version,
            ffprobe=probe_data,
            contract_passed=contract_passed,
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

