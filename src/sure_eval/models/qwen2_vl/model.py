from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ModelLoadError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


class ConfigurationError(ValueError):
    pass


@dataclass
class VLMResult:
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not self.text.strip():
            raise ValueError("text must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = config or {}
        self.model_id = self.config.get("model_id", "Qwen/Qwen2-VL-2B-Instruct")
        self.default_prompt = self.config.get("prompt", "Describe this image briefly.")
        self.max_new_tokens = int(self.config.get("max_new_tokens", 128))
        self.hf_home = self.config.get("hf_home")
        self._model = None
        self._processor = None
        self._last_error: str | None = None

    def _apply_env(self) -> None:
        if self.hf_home:
            os.environ["HF_HOME"] = str(self.hf_home)

    def load(self) -> None:
        if self._model is not None and self._processor is not None:
            return

        self._apply_env()

        try:
            from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
        except ImportError as exc:
            raise ModelLoadError(f"Missing runtime dependency: {exc}") from exc

        try:
            self._model = Qwen2VLForConditionalGeneration.from_pretrained(
                self.model_id,
                torch_dtype="auto",
                device_map="auto",
            )
            self._processor = AutoProcessor.from_pretrained(self.model_id)
        except Exception as exc:
            self._last_error = str(exc)
            raise ModelLoadError(f"Failed to load {self.model_id}: {exc}") from exc

    def _normalize_request(self, input_data: Any) -> tuple[str, str, int]:
        if isinstance(input_data, (str, Path)):
            image_path = str(input_data)
            prompt = self.default_prompt
            max_new_tokens = self.max_new_tokens
        elif isinstance(input_data, dict):
            image_path = input_data.get("image_path") or input_data.get("image")
            prompt = input_data.get("prompt", self.default_prompt)
            max_new_tokens = int(input_data.get("max_new_tokens", self.max_new_tokens))
        else:
            raise ConfigurationError("input_data must be an image path or a dict")

        if not image_path:
            raise ConfigurationError("image_path is required")

        resolved = Path(image_path).expanduser().resolve()
        if not resolved.exists():
            raise ConfigurationError(f"Image not found: {resolved}")

        return resolved.as_uri(), prompt, max_new_tokens

    def predict(self, input_data: Any) -> dict[str, Any]:
        self.load()

        try:
            from qwen_vl_utils import process_vision_info
        except ImportError as exc:
            raise InferenceError(f"Missing qwen_vl_utils dependency: {exc}") from exc

        image_uri, prompt, max_new_tokens = self._normalize_request(input_data)

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image_uri},
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        try:
            text = self._processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
            image_inputs, video_inputs = process_vision_info(messages)
            inputs = self._processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt",
            )
            inputs = inputs.to(self._model.device)
            generated_ids = self._model.generate(**inputs, max_new_tokens=max_new_tokens)
            generated_ids_trimmed = [
                out_ids[len(in_ids):]
                for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
            ]
            output_text = self._processor.batch_decode(
                generated_ids_trimmed,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )
            result = VLMResult(
                text=output_text[0] if isinstance(output_text, list) and output_text else ""
            )
            return result.to_dict()
        except Exception as exc:
            self._last_error = str(exc)
            raise InferenceError(f"Failed to run inference: {exc}") from exc

    def healthcheck(self) -> dict[str, Any]:
        if self._model is not None and self._processor is not None:
            return {
                "status": "ready",
                "message": "Model and processor are loaded.",
                "model_loaded": True,
            }
        if self._last_error:
            return {
                "status": "error",
                "message": self._last_error,
                "model_loaded": False,
            }
        return {
            "status": "loading",
            "message": "Model has not been loaded yet.",
            "model_loaded": False,
        }
