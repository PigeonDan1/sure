"""DashScope (Alibaba Cloud Bailian) API Adapter for SURE-EVAL.

This adapter allows using DashScope models directly as tools for evaluation.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from sure_eval.core.config import Config
from sure_eval.core.logging import get_logger

logger = get_logger(__name__)


class DashScopeAdapter:
    """
    Adapter for Alibaba Cloud DashScope (Bailian) API.
    
    Supports models:
    - qwen-plus: General purpose
    - qwen-max: High capability
    - qwen-turbo: Fast inference
    - qwen-audio-asr: ASR specific
    - qwen-audio-chat: Audio understanding
    """
    
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        config: Config | None = None,
    ) -> None:
        self.config = config or Config.from_env()
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = base_url
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        
        logger.info("DashScopeAdapter initialized")
    
    def _encode_audio(self, audio_path: str | Path) -> str:
        """Encode audio file to base64."""
        with open(audio_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    
    def transcribe(
        self,
        audio_path: str | Path,
        model: str = "qwen-audio-asr",
        language: str | None = None,
    ) -> dict[str, Any]:
        """
        Transcribe audio to text (ASR).
        
        Args:
            audio_path: Path to audio file
            model: Model to use
            language: Optional language hint (zh, en, etc.)
            
        Returns:
            Dict with 'text', 'confidence', etc.
        """
        audio_path = Path(audio_path)
        
        logger.info("Transcribing with DashScope", path=str(audio_path), model=model)
        
        # For audio models, we can use base64 encoding or file URL
        # Here we use base64 for simplicity
        audio_base64 = self._encode_audio(audio_path)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "audio",
                        "audio": f"data:audio/wav;base64,{audio_base64}",
                    },
                    {
                        "type": "text",
                        "text": "Transcribe this audio to text." if not language else f"Transcribe this audio to text in {language}.",
                    },
                ],
            }
        ]
        
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
            )
            
            text = response.choices[0].message.content
            
            return {
                "text": text,
                "model": model,
                "success": True,
            }
            
        except Exception as e:
            logger.error("Transcription failed", error=str(e))
            return {
                "text": "",
                "error": str(e),
                "success": False,
            }
    
    def translate_audio(
        self,
        audio_path: str | Path,
        target_lang: str = "en",
        model: str = "qwen-audio-chat",
    ) -> dict[str, Any]:
        """
        Translate audio to text in target language (S2TT).
        
        Args:
            audio_path: Path to audio file
            target_lang: Target language code
            model: Model to use
            
        Returns:
            Dict with 'text', 'source_lang', 'target_lang'
        """
        audio_path = Path(audio_path)
        
        logger.info(
            "Translating audio with DashScope",
            path=str(audio_path),
            target=target_lang,
        )
        
        audio_base64 = self._encode_audio(audio_path)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "audio",
                        "audio": f"data:audio/wav;base64,{audio_base64}",
                    },
                    {
                        "type": "text",
                        "text": f"Translate this audio to {target_lang}.",
                    },
                ],
            }
        ]
        
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
            )
            
            text = response.choices[0].message.content
            
            return {
                "text": text,
                "target_lang": target_lang,
                "source_lang": "auto",
                "success": True,
            }
            
        except Exception as e:
            logger.error("Translation failed", error=str(e))
            return {
                "text": "",
                "error": str(e),
                "success": False,
            }
    
    def analyze_emotion(
        self,
        audio_path: str | Path,
        model: str = "qwen-audio-chat",
    ) -> dict[str, Any]:
        """
        Analyze emotion from audio (SER).
        
        Args:
            audio_path: Path to audio file
            model: Model to use
            
        Returns:
            Dict with 'emotion', 'confidence'
        """
        audio_path = Path(audio_path)
        
        logger.info("Analyzing emotion with DashScope", path=str(audio_path))
        
        audio_base64 = self._encode_audio(audio_path)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "audio",
                        "audio": f"data:audio/wav;base64,{audio_base64}",
                    },
                    {
                        "type": "text",
                        "text": "Analyze the emotion in this audio. Respond with one word: neutral, happy, sad, angry, or fearful.",
                    },
                ],
            }
        ]
        
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
            )
            
            emotion = response.choices[0].message.content.strip().lower()
            
            # Normalize emotion
            emotion_map = {
                "neutral": "neu",
                "happy": "hap",
                "happiness": "hap",
                "sad": "sad",
                "sadness": "sad",
                "angry": "ang",
                "anger": "ang",
                "fearful": "fea",
                "fear": "fea",
            }
            
            normalized = emotion_map.get(emotion, emotion)
            
            return {
                "emotion": normalized,
                "raw": emotion,
                "success": True,
            }
            
        except Exception as e:
            logger.error("Emotion analysis failed", error=str(e))
            return {
                "emotion": "unknown",
                "error": str(e),
                "success": False,
            }
    
    def analyze_content(
        self,
        audio_path: str | Path,
        question: str,
        model: str = "qwen-audio-chat",
    ) -> dict[str, Any]:
        """
        General audio content analysis.
        
        Args:
            audio_path: Path to audio file
            question: Question to ask about the audio
            model: Model to use
            
        Returns:
            Dict with 'answer'
        """
        audio_path = Path(audio_path)
        audio_base64 = self._encode_audio(audio_path)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "audio",
                        "audio": f"data:audio/wav;base64,{audio_base64}",
                    },
                    {
                        "type": "text",
                        "text": question,
                    },
                ],
            }
        ]
        
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
            )
            
            answer = response.choices[0].message.content
            
            return {
                "answer": answer,
                "success": True,
            }
            
        except Exception as e:
            logger.error("Analysis failed", error=str(e))
            return {
                "answer": "",
                "error": str(e),
                "success": False,
            }
    
    def batch_process(
        self,
        audio_paths: list[str | Path],
        task: str = "transcribe",
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Process multiple audio files.
        
        Args:
            audio_paths: List of audio file paths
            task: Task type (transcribe, translate, analyze_emotion)
            **kwargs: Additional arguments for the task
            
        Returns:
            List of results
        """
        results = []
        
        for i, path in enumerate(audio_paths):
            logger.info(f"Processing {i+1}/{len(audio_paths)}: {path}")
            
            if task == "transcribe":
                result = self.transcribe(path, **kwargs)
            elif task == "translate":
                result = self.translate_audio(path, **kwargs)
            elif task == "analyze_emotion":
                result = self.analyze_emotion(path, **kwargs)
            else:
                raise ValueError(f"Unknown task: {task}")
            
            results.append({
                "path": str(path),
                **result,
            })
        
        return results


class DashScopeToolWrapper:
    """
    Wrapper to use DashScope adapter as a tool in the evaluation framework.
    
    This allows DashScope models to be evaluated like any other tool.
    """
    
    def __init__(
        self,
        name: str = "dashscope_qwen",
        api_key: str | None = None,
        model: str = "qwen-audio-asr",
        config: Config | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.adapter = DashScopeAdapter(api_key=api_key, config=config)
        
        logger.info("DashScopeToolWrapper initialized", name=name, model=model)
    
    def invoke(
        self,
        audio_path: str | Path,
        task: str = "ASR",
        **kwargs,
    ) -> dict[str, Any]:
        """
        Invoke the tool on an audio file.
        
        This method conforms to the tool interface expected by AutonomousEvaluator.
        """
        if task == "ASR":
            return self.adapter.transcribe(
                audio_path,
                model=self.model,
                language=kwargs.get("language"),
            )
        elif task == "S2TT":
            return self.adapter.translate_audio(
                audio_path,
                target_lang=kwargs.get("target_lang", "en"),
                model=self.model,
            )
        elif task == "SER":
            return self.adapter.analyze_emotion(audio_path, model=self.model)
        else:
            return {
                "text": "",
                "error": f"Task {task} not supported",
                "success": False,
            }
    
    def batch_invoke(
        self,
        audio_paths: list[str | Path],
        task: str = "ASR",
        **kwargs,
    ) -> list[dict[str, Any]]:
        """Batch invoke on multiple files."""
        return self.adapter.batch_process(audio_paths, task, **kwargs)
