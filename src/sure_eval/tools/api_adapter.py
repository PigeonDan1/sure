"""API adapter for external tool/model APIs.

This module provides adapters for calling external APIs.
Users can implement their own adapters by subclassing BaseAPIAdapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from sure_eval.core.config import Config
from sure_eval.core.logging import get_logger

logger = get_logger(__name__)


class BaseAPIAdapter(ABC):
    """Base class for API adapters."""
    
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
        self.api_config = self.config.api.tool_api
    
    @abstractmethod
    def transcribe(self, audio_path: str | Path) -> dict[str, Any]:
        """Transcribe audio (ASR)."""
        pass
    
    @abstractmethod
    def diarize(self, audio_path: str | Path) -> dict[str, Any]:
        """Diarize audio (SD)."""
        pass
    
    @abstractmethod
    def translate(self, audio_path: str | Path, target_lang: str = "en") -> dict[str, Any]:
        """Translate audio (S2TT)."""
        pass
    
    @abstractmethod
    def recognize_emotion(self, audio_path: str | Path) -> dict[str, Any]:
        """Recognize emotion (SER)."""
        pass


class DefaultAPIAdapter(BaseAPIAdapter):
    """Default API adapter using HTTP requests.
    
    Users should customize this adapter to match their API specifications.
    """
    
    def __init__(self, config: Config | None = None) -> None:
        super().__init__(config)
        self.base_url = self.api_config.get("base_url", "")
        self.api_key = self.api_config.get("api_key", "")
        self.timeout = self.api_config.get("timeout", 60)
        
        if not self.base_url:
            logger.warning("API base_url not configured")
    
    def _make_request(self, endpoint: str, data: dict) -> dict[str, Any]:
        """Make API request."""
        import httpx
        
        url = f"{self.base_url}/{endpoint}"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        
        try:
            response = httpx.post(
                url,
                json=data,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("API request failed", error=str(e), endpoint=endpoint)
            raise
    
    def transcribe(self, audio_path: str | Path) -> dict[str, Any]:
        """Transcribe audio using API."""
        # TODO: Implement based on actual API spec
        logger.info("Transcribing", path=str(audio_path))
        
        # Placeholder implementation
        return {
            "text": "transcription_result",
            "confidence": 0.95,
        }
    
    def diarize(self, audio_path: str | Path) -> dict[str, Any]:
        """Diarize audio using API."""
        # TODO: Implement based on actual API spec
        logger.info("Diarizing", path=str(audio_path))
        
        # Placeholder implementation
        return {
            "rttm": "speaker_turns",
            "num_speakers": 2,
        }
    
    def translate(self, audio_path: str | Path, target_lang: str = "en") -> dict[str, Any]:
        """Translate audio using API."""
        # TODO: Implement based on actual API spec
        logger.info("Translating", path=str(audio_path), target=target_lang)
        
        # Placeholder implementation
        return {
            "text": "translation_result",
            "source_lang": "auto",
            "target_lang": target_lang,
        }
    
    def recognize_emotion(self, audio_path: str | Path) -> dict[str, Any]:
        """Recognize emotion using API."""
        # TODO: Implement based on actual API spec
        logger.info("Recognizing emotion", path=str(audio_path))
        
        # Placeholder implementation
        return {
            "emotion": "neutral",
            "confidence": 0.8,
        }


class APIAdapterRegistry:
    """Registry for API adapters."""
    
    _adapters: dict[str, type[BaseAPIAdapter]] = {
        "default": DefaultAPIAdapter,
    }
    
    @classmethod
    def register(cls, name: str, adapter_class: type[BaseAPIAdapter]) -> None:
        """Register a new adapter."""
        cls._adapters[name] = adapter_class
    
    @classmethod
    def get_adapter(cls, name: str = "default", config: Config | None = None) -> BaseAPIAdapter:
        """Get an adapter instance."""
        adapter_class = cls._adapters.get(name, DefaultAPIAdapter)
        return adapter_class(config)
    
    @classmethod
    def list_adapters(cls) -> list[str]:
        """List available adapters."""
        return list(cls._adapters.keys())


# For user customization:
# 
# class MyCustomAdapter(BaseAPIAdapter):
#     def transcribe(self, audio_path):
#         # Implement your API call
#         pass
#
# # Register the adapter
# APIAdapterRegistry.register("my_adapter", MyCustomAdapter)
#
# # Use in evaluator
# adapter = APIAdapterRegistry.get_adapter("my_adapter")
