"""SURE wrapper skeleton for FunAudioLLM/Fun-CosyVoice3-0.5B-2512."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent
SOURCE_DIR = MODEL_DIR / ".runtime/source/CosyVoice"
DEFAULT_MODEL_DIR = SOURCE_DIR / "pretrained_models/Fun-CosyVoice3-0.5B"


@dataclass
class TTSResult:
    text: str
    prompt_text: str
    prompt_audio_path: str
    tts_speech: Any
    sample_rate: int | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tts_speech"] = _summarize_tensor_like(self.tts_speech)
        return payload


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.source_dir = Path(self.config.get("source_dir") or SOURCE_DIR)
        self.model_dir = Path(
            self.config.get("model_dir")
            or os.environ.get("MODEL_PATH")
            or _weights_manifest_path()
            or DEFAULT_MODEL_DIR
        )
        self.device = self.config.get("device") or os.environ.get("DEVICE") or "cuda"
        self._model: Any | None = None
        self.model_loaded = False

    def load(self) -> None:
        if not self.source_dir.exists():
            raise FileNotFoundError(
                f"Missing CosyVoice source tree: {self.source_dir}. "
                "Fetch https://github.com/FunAudioLLM/CosyVoice at commit "
                "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc into this path."
            )
        if not self.model_dir.exists():
            raise FileNotFoundError(
                f"Missing CosyVoice model weights: {self.model_dir}. "
                "The phase-1 input requires pretrained_models/Fun-CosyVoice3-0.5B."
            )

        matcha_path = self.source_dir / "third_party/Matcha-TTS"
        for path in [self.source_dir, matcha_path]:
            path_text = str(path)
            if path.exists() and path_text not in sys.path:
                sys.path.insert(0, path_text)

        from cosyvoice.cli.cosyvoice import AutoModel

        self._model = AutoModel(model_dir=str(self.model_dir))
        self.model_loaded = True

    def predict(self, input_data: dict[str, Any] | str) -> TTSResult:
        payload = input_data if isinstance(input_data, dict) else {"text": input_data}
        text = str(payload.get("text") or "")
        prompt_text = str(payload.get("prompt_text") or "")
        prompt_audio_path = Path(payload.get("prompt_audio_path") or SOURCE_DIR / "asset/zero_shot_prompt.wav")
        stream = bool(payload.get("stream", False))

        if not text:
            raise ValueError("text is required")
        if not prompt_text:
            raise ValueError("prompt_text is required for zero-shot CosyVoice inference")
        if not prompt_audio_path.exists():
            raise FileNotFoundError(f"Missing prompt audio fixture: {prompt_audio_path}")

        if not self.model_loaded:
            self.load()
        assert self._model is not None

        result = next(
            self._model.inference_zero_shot(
                text,
                prompt_text,
                str(prompt_audio_path),
                stream=stream,
            )
        )
        if "tts_speech" not in result:
            raise RuntimeError(f"CosyVoice result missing tts_speech: keys={sorted(result)}")
        return TTSResult(
            text=text,
            prompt_text=prompt_text,
            prompt_audio_path=str(prompt_audio_path),
            tts_speech=result["tts_speech"],
            sample_rate=_coerce_int(
                result.get("sample_rate")
                or result.get("sampling_rate")
                or getattr(self._model, "sample_rate", None)
            ),
            raw={key: value for key, value in result.items() if key != "tts_speech"},
        )

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "loaded" if self.model_loaded else "ready",
            "model_loaded": self.model_loaded,
            "source_dir": str(self.source_dir),
            "model_dir": str(self.model_dir),
            "device": self.device,
        }


def _weights_manifest_path() -> str | None:
    manifest_path = MODEL_DIR / "artifacts/weights_manifest.json"
    if not manifest_path.exists():
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    resolved = payload.get("resolved_local_model_path")
    if resolved and Path(resolved).exists():
        return resolved
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _summarize_tensor_like(value: Any) -> dict[str, Any]:
    shape = getattr(value, "shape", None)
    numel = None
    if hasattr(value, "numel"):
        try:
            numel = int(value.numel())
        except Exception:
            numel = None
    elif hasattr(value, "__len__"):
        try:
            numel = len(value)
        except Exception:
            numel = None
    return {
        "type": type(value).__name__,
        "shape": list(shape) if shape is not None else None,
        "numel": numel,
        "nonempty": bool(numel is None or numel > 0),
    }
