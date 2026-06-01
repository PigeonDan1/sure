from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass
class TranscriptionResult:
    text: str
    raw: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TextTaskResult:
    text: str
    task: str
    label: str | None = None
    raw: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model_id = self.config.get("model_id") or os.environ.get(
            "MODEL_ID", "moonshotai/Kimi-Audio-7B-Instruct"
        )
        model_path = self.config.get("model_path") or os.environ.get(
            "KIMI_AUDIO_MODEL_PATH",
            ".runtime/modelscope_cache/models/moonshotai/Kimi-Audio-7B-Instruct",
        )
        self.model_path = self._resolve_model_relative_path(model_path)
        self.device = self.config.get("device") or os.environ.get("DEVICE", "auto")
        self.load_detokenizer = bool(self.config.get("load_detokenizer", False))
        self.device_map = self.config.get("device_map") or os.environ.get(
            "KIMI_AUDIO_DEVICE_MAP"
        )
        load_in_8bit_value = self.config.get("load_in_8bit", os.environ.get("KIMI_AUDIO_LOAD_IN_8BIT", "0"))
        self.load_in_8bit = str(load_in_8bit_value).lower() in {"1", "true", "yes", "on"}
        self.max_new_tokens = int(self.config.get("max_new_tokens", 128))
        self._model = None

    def _resolve_model_relative_path(self, path_value: str | os.PathLike[str]) -> str:
        path = Path(path_value).expanduser()
        if path.is_absolute():
            return str(path)
        return str((Path(__file__).resolve().parent / path).resolve())

    def _resolve_model_path(self) -> str:
        return self.model_path

    def _validate_weights_present(self) -> None:
        root = Path(self.model_path)
        required = [
            root / "config.json",
            root / "model.safetensors.index.json",
            root / "whisper-large-v3" / "model.safetensors",
            root / "audio_detokenizer" / "model.pt",
            root / "vocoder" / "model.pt",
        ]
        required.extend(root / f"model-{index}-of-35.safetensors" for index in range(1, 36))
        missing = [str(path.relative_to(root)) for path in required if not path.exists()]
        if missing:
            raise ModelLoadError(f"Missing Kimi-Audio weight files: {missing[:8]}")

    def _cuda_ready(self) -> bool:
        try:
            import torch

            return bool(torch.cuda.is_available() and torch.cuda.device_count() > 0)
        except Exception:
            return False

    def load(self) -> None:
        if self._model is not None:
            return
        self._validate_weights_present()
        if not self._cuda_ready():
            raise ModelLoadError(
                "Kimi-Audio requires CUDA for the repo-native runtime, but no CUDA "
                "device is visible in this shell."
            )
        try:
            from kimia_infer.api.kimia import KimiAudio

            self._model = KimiAudio(
                model_path=self.model_path,
                load_detokenizer=self.load_detokenizer,
                device_map=self.device_map,
                load_in_8bit=self.load_in_8bit,
            )
        except Exception as exc:
            raise ModelLoadError(f"Failed to load Kimi-Audio from {self.model_path}: {exc}") from exc

    def predict(self, input_data: str) -> TranscriptionResult:
        if self._model is None:
            self.load()
        messages = [
            {
                "role": "user",
                "message_type": "text",
                "content": "Please transcribe the following audio:",
            },
            {"role": "user", "message_type": "audio", "content": input_data},
        ]
        try:
            _, text = self._model.generate(
                messages,
                output_type="text",
                text_temperature=0.0,
                text_top_k=5,
                max_new_tokens=self.max_new_tokens,
            )
            return TranscriptionResult(text=text or "", raw={"model_id": self.model_id})
        except Exception as exc:
            raise InferenceError(f"Kimi-Audio inference failed: {exc}") from exc

    def _generate_text(
        self,
        audio_path: str,
        instruction: str,
        *,
        task: str,
        max_new_tokens: int | None = None,
    ) -> TextTaskResult:
        if self._model is None:
            self.load()
        messages = [
            {"role": "user", "message_type": "text", "content": instruction},
            {"role": "user", "message_type": "audio", "content": audio_path},
        ]
        try:
            _, text = self._model.generate(
                messages,
                output_type="text",
                text_temperature=0.0,
                text_top_k=5,
                max_new_tokens=max_new_tokens or self.max_new_tokens,
            )
            return TextTaskResult(
                text=text or "",
                task=task,
                raw={"model_id": self.model_id, "instruction": instruction},
            )
        except Exception as exc:
            raise InferenceError(f"Kimi-Audio {task} inference failed: {exc}") from exc

    def translate(
        self,
        audio_path: str,
        *,
        source_language: str = "auto",
        target_language: str = "zh",
    ) -> TextTaskResult:
        instruction = (
            "Translate the speech in the following audio into "
            f"{target_language}. Return only the translated text."
        )
        if source_language != "auto":
            instruction = (
                f"The source speech language is {source_language}. " + instruction
            )
        return self._generate_text(audio_path, instruction, task="S2TT")

    def recognize_emotion(self, audio_path: str) -> TextTaskResult:
        result = self._generate_text(
            audio_path,
            "Recognize the speaker emotion in the following audio. "
            "Answer with exactly one label from: neu, hap, ang, sad.",
            task="SER",
            max_new_tokens=16,
        )
        result.label = self._extract_label(
            result.text,
            {
                "neu": {"neu", "neutral", "calm"},
                "hap": {"hap", "happy", "happiness", "joy", "joyful"},
                "ang": {"ang", "angry", "anger"},
                "sad": {"sad", "sadness"},
            },
        )
        return result

    def recognize_gender(self, audio_path: str) -> TextTaskResult:
        result = self._generate_text(
            audio_path,
            "Recognize the speaker gender in the following audio. "
            "Answer with exactly one label from: male, female.",
            task="GR",
            max_new_tokens=16,
        )
        result.label = self._extract_label(
            result.text,
            {
                "male": {"male", "man", "m"},
                "female": {"female", "woman", "f"},
            },
        )
        return result

    def understand(
        self,
        audio_path: str,
        *,
        prompt: str | None = None,
    ) -> TextTaskResult:
        instruction = prompt or (
            "Listen to the following audio and answer the question in the audio. "
            "If it is a multiple-choice question, answer only one option letter "
            "from A, B, C, or D."
        )
        return self._generate_text(audio_path, instruction, task="SLU")

    def _extract_label(self, text: str, label_aliases: dict[str, set[str]]) -> str | None:
        normalized = "".join(
            char.lower() if char.isalnum() else " " for char in text
        )
        tokens = normalized.split()
        for canonical, aliases in label_aliases.items():
            if canonical in tokens:
                return canonical
            if any(alias in tokens for alias in aliases):
                return canonical
        compact = "".join(tokens)
        for canonical, aliases in label_aliases.items():
            if compact in aliases:
                return canonical
        return None

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model is not None else "loading",
            "message": "Model loaded" if self._model is not None else "Model not loaded",
            "model_loaded": self._model is not None,
            "model_id": self.model_id,
            "model_path": self.model_path,
            "device": self.device,
            "cuda_available": self._cuda_ready(),
            "load_detokenizer": self.load_detokenizer,
            "device_map": self.device_map,
            "load_in_8bit": self.load_in_8bit,
        }
