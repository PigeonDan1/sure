from __future__ import annotations

import os
import re
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


def target_language_name(target_language: str) -> str:
    return {
        "zh": "Chinese",
        "zh-cn": "Simplified Chinese",
        "zh_cn": "Simplified Chinese",
        "cmn": "Chinese",
        "en": "English",
        "ja": "Japanese",
        "jp": "Japanese",
        "ko": "Korean",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
        "it": "Italian",
        "ru": "Russian",
        "pt": "Portuguese",
        "ar": "Arabic",
        "hi": "Hindi",
    }.get(target_language.strip().lower(), target_language)


def build_translation_instruction(
    *,
    transcript: str,
    source_language: str = "auto",
    target_language: str = "zh",
) -> str:
    source_part = (
        "The source language is auto-detected."
        if source_language == "auto"
        else f"The source language is {target_language_name(source_language)}."
    )
    target = target_language_name(target_language)
    source = target_language_name(source_language) if source_language != "auto" else "source"
    return (
        f"{source_part} Translate this {source} sentence into {target}. "
        f"Output only the {target} translation.\n\n"
        f"{transcript}"
    )


def extract_choice_label(text: str) -> str | None:
    text = clean_generated_text(text)
    stripped = text.strip()
    if not stripped:
        return None
    normalized = stripped.upper()
    single = re.sub(r"[\s。．.！!？?、,，:：;；()（）\\[\\]{}<>《》\"'`]+", "", normalized)
    if single in {"A", "B", "C", "D"}:
        return single
    edge_stripped = normalized.strip(" \t\r\n。．.！!？?、,，:：;；()（）[]{}<>《》\"'`")
    if edge_stripped in {"A", "B", "C", "D"}:
        return edge_stripped
    patterns = [
        r"^(?:答案|回答|选择|选项|应选|答案是|我选)\s*[:：]?\s*([ABCD])\s*[。．.!！)]*$",
        r"^(?:ANSWER|OPTION|CHOICE|THE ANSWER IS)\s*[:：]?\s*([ABCD])\s*[。．.!！)]*$",
        r"^([ABCD])\s*[。．.)、]?\s*$",
    ]
    for pattern in patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def clean_generated_text(text: str) -> str:
    if not text:
        return ""
    stop_markers = [
        "<|im_msg_end|>",
        "<|im_end|>",
        "<|endoftext|>",
        "\nYou are an AI assistant.",
    ]
    cleaned = text
    for marker in stop_markers:
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0]
    return cleaned.strip()


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
            return TranscriptionResult(text=clean_generated_text(text or ""), raw={"model_id": self.model_id})
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
                text=clean_generated_text(text or ""),
                task=task,
                raw={"model_id": self.model_id, "instruction": instruction},
            )
        except Exception as exc:
            raise InferenceError(f"Kimi-Audio {task} inference failed: {exc}") from exc

    def _generate_text_only(
        self,
        instruction: str,
        *,
        task: str,
        max_new_tokens: int | None = None,
    ) -> TextTaskResult:
        if self._model is None:
            self.load()
        messages = [{"role": "user", "message_type": "text", "content": instruction}]
        try:
            _, text = self._model.generate(
                messages,
                output_type="text",
                text_temperature=0.0,
                text_top_k=5,
                max_new_tokens=max_new_tokens or self.max_new_tokens,
            )
            return TextTaskResult(
                text=clean_generated_text(text or ""),
                task=task,
                raw={"model_id": self.model_id, "instruction": instruction},
            )
        except Exception as exc:
            raise InferenceError(f"Kimi-Audio {task} text inference failed: {exc}") from exc

    def translate(
        self,
        audio_path: str,
        *,
        source_language: str = "auto",
        target_language: str = "zh",
    ) -> TextTaskResult:
        transcript_result = self.predict(audio_path)
        instruction = build_translation_instruction(
            transcript=transcript_result.text,
            source_language=source_language,
            target_language=target_language,
        )
        result = self._generate_text_only(instruction, task="S2TT")
        result.raw = {
            **(result.raw or {}),
            "stage": "asr_then_text_translate",
            "transcript": transcript_result.text,
            "source_language": source_language,
            "target_language": target_language,
        }
        return result

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
        base_prompt = prompt or (
            "Listen to the audio and answer the semantic understanding task."
        )
        instruction = (
            f"{base_prompt}\n\n"
            "The audio may contain a question, context, and answer choices. "
            "Reason silently from the audio and output exactly one uppercase letter: A, B, C, or D. "
            "Do not explain."
        )
        result = self._generate_text(audio_path, instruction, task="SLU", max_new_tokens=16)
        result.label = extract_choice_label(result.text)
        result.raw = {
            **(result.raw or {}),
            "stage": "direct_audio_understand",
        }
        return result

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
