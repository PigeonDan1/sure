"""VibeVoice-ASR model wrapper for SURE-EVAL."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import torch
from transformers import AutoProcessor, VibeVoiceAsrForConditionalGeneration


class ModelWrapper:
    """Wrapper for microsoft/VibeVoice-ASR."""

    def __init__(self, model_path: Optional[str] = None, device: Optional[str] = None):
        self.model_path = model_path or self._resolve_model_path()
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.processor: Optional[AutoProcessor] = None
        self.model: Optional[VibeVoiceAsrForConditionalGeneration] = None

    @staticmethod
    def _resolve_model_path() -> str:
        """Resolve model path relative to this file."""
        here = Path(__file__).parent
        # Prefer HF version (complete transformers-compatible package)
        hf = here / ".runtime" / "microsoft" / "VibeVoice-ASR-HF"
        if hf.exists():
            return str(hf)
        # Fallback to unified directory or original weights
        unified = here / ".runtime" / "microsoft" / "VibeVoice-ASR-unified"
        if unified.exists():
            return str(unified)
        original = here / ".runtime" / "microsoft" / "VibeVoice-ASR"
        return str(original)

    def load(self) -> None:
        """Load processor and model."""
        if self.processor is not None and self.model is not None:
            return

        print(f"Loading VibeVoice-ASR from {self.model_path} on {self.device}...")
        self.processor = AutoProcessor.from_pretrained(self.model_path)

        dtype = torch.float16 if self.device == "cuda" else torch.float32
        if self.device == "cuda":
            self.model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
                self.model_path,
                torch_dtype=dtype,
                device_map="auto",
            )
        else:
            self.model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
                self.model_path,
                torch_dtype=dtype,
                device_map="cpu",
            )
        print("Model loaded.")

    def predict(self, audio_path: str, max_new_tokens: int = 256) -> dict:
        """Run ASR inference on an audio file.

        Args:
            audio_path: Path to the audio file.
            max_new_tokens: Maximum number of new tokens to generate.

        Returns:
            Dict with key "text" containing the transcription.
        """
        if self.processor is None or self.model is None:
            self.load()

        inputs = self.processor.apply_transcription_request(audio=audio_path)
        for k, v in inputs.items():
            if hasattr(v, "to"):
                inputs[k] = v.to(self.model.device)

        output_ids = self.model.generate(**inputs, max_new_tokens=max_new_tokens)
        generated_ids = output_ids[:, inputs["input_ids"].shape[1] :]
        transcription = self.processor.decode(
            generated_ids, return_format="transcription_only"
        )[0]

        return {"text": transcription}

    def transcribe(self, audio_path: str, max_new_tokens: int = 256) -> str:
        """Convenience alias for predict returning text only."""
        result = self.predict(audio_path, max_new_tokens=max_new_tokens)
        return result["text"]
