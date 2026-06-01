"""
Qwen3-Omni Model Wrapper.

API client for Qwen3-Omni multi-modal model via DashScope.
Supports text and audio generation.
"""

from __future__ import annotations

import os
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class OmniChatResult:
    """Result of Omni chat."""
    text: str
    audio_path: str | None = None
    audio_data: bytes | None = None


class Qwen3OmniModel:
    """Wrapper for Qwen3-Omni API."""
    
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "qwen3-omni-flash",
        voice: str = "Cherry",
        audio_format: str = "wav",
        sample_rate: int = 24000,
    ):
        """
        Initialize Qwen3-Omni model.
        
        Args:
            api_key: DashScope API key (or set DASHSCOPE_API_KEY env var)
            base_url: API base URL
            model: Model ID
            voice: Voice for audio generation
            audio_format: Audio format (wav, mp3, etc.)
            sample_rate: Audio sample rate
        """
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.base_url = base_url or os.environ.get(
            "DASHSCOPE_BASE_URL",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
        self.model = model
        self.voice = voice
        self.audio_format = audio_format
        self.sample_rate = sample_rate
        
        self._client = None
    
    def _get_client(self):
        """Lazy initialize OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError(
                    "openai not installed. Run: pip install openai"
                )
            
            if not self.api_key:
                raise RuntimeError(
                    "DASHSCOPE_API_KEY not set. Please provide API key."
                )
            
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client
    
    def chat(
        self,
        message: str,
        generate_audio: bool = True,
        output_audio_path: str | Path | None = None,
    ) -> OmniChatResult:
        """
        Send chat message and get response.
        
        Args:
            message: Text message to send
            generate_audio: Whether to generate audio response
            output_audio_path: Path to save audio file
            
        Returns:
            OmniChatResult with text and optional audio
        """
        import numpy as np
        
        client = self._get_client()
        
        # Build request
        request_params = {
            "model": self.model,
            "messages": [{"role": "user", "content": message}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        
        if generate_audio:
            request_params["modalities"] = ["text", "audio"]
            request_params["audio"] = {
                "voice": self.voice,
                "format": self.audio_format
            }
        
        # Call API
        completion = client.chat.completions.create(**request_params)
        
        # Process response
        text_response = ""
        audio_base64 = ""
        
        for chunk in completion:
            if chunk.choices and chunk.choices[0].delta.content:
                text_response += chunk.choices[0].delta.content
            
            if (chunk.choices and 
                hasattr(chunk.choices[0].delta, "audio") and 
                chunk.choices[0].delta.audio):
                audio_base64 += chunk.choices[0].delta.audio.get("data", "")
        
        # Save audio if generated
        audio_data = None
        saved_path = None
        
        if generate_audio and audio_base64 and output_audio_path:
            wav_bytes = base64.b64decode(audio_base64)
            audio_data = wav_bytes
            
            # Save to file
            output_path = Path(output_audio_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            import soundfile as sf
            audio_np = np.frombuffer(wav_bytes, dtype=np.int16)
            sf.write(str(output_path), audio_np, samplerate=self.sample_rate)
            saved_path = str(output_path)
        
        return OmniChatResult(
            text=text_response,
            audio_path=saved_path,
            audio_data=audio_data,
        )
    
    def chat_text_only(self, message: str) -> str:
        """
        Send chat message and get text response only (faster).
        
        Args:
            message: Text message to send
            
        Returns:
            Text response
        """
        result = self.chat(message, generate_audio=False)
        return result.text
