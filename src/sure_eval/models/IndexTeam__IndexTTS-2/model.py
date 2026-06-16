"""SURE wrapper for IndexTeam/IndexTTS-2."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent


@dataclass
class PredictionResult:
    text: str = ""
    audio_path: str = ""
    language: str = "auto"
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.model_path = Path(
            self.config.get("model_path")
            or _weights_manifest_path()
            or MODEL_DIR / ".runtime/modelscope_cache/IndexTeam/IndexTTS-2"
        )
        self.device = self.config.get("device") or os.environ.get("DEVICE") or "cpu"
        self.model_loaded = False
        self._model = None

    def load(self) -> None:
        os.environ.setdefault("MPLCONFIGDIR", str(MODEL_DIR / ".runtime/matplotlib"))
        os.environ.setdefault("HF_HOME", str((MODEL_DIR / ".runtime/huggingface").resolve()))
        os.environ.setdefault("HF_HUB_CACHE", str((MODEL_DIR / ".runtime/huggingface/hub").resolve()))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("INDEXTTS2_WRAPPER_DIR", str(MODEL_DIR))

        cfg_path = self.model_path / "config.yaml"
        for item in [
            cfg_path,
            self.model_path / "gpt.pth",
            self.model_path / "s2mel.pth",
            self.model_path / "bpe.model",
            self.model_path / "wav2vec2bert_stats.pt",
            self.model_path / "feat1.pt",
            self.model_path / "feat2.pt",
            self.model_path / "qwen0.6bemo4-merge/config.json",
        ]:
            if not item.exists():
                raise FileNotFoundError(f"Missing IndexTTS-2 runtime file: {item}")

        source_path = MODEL_DIR / ".runtime/source/index-tts"
        if source_path.exists():
            sys.path.insert(0, str(source_path))

        from indextts.infer_v2 import IndexTTS2

        self._model = IndexTTS2(
            cfg_path=str(cfg_path),
            model_dir=str(self.model_path),
            device=self.device,
            use_fp16=False,
            use_cuda_kernel=False,
            use_deepspeed=False,
            use_accel=False,
            use_torch_compile=False,
        )
        self.model_loaded = True

    def predict(self, input_data: Any) -> PredictionResult:
        payload = input_data if isinstance(input_data, dict) else {"text": input_data}
        text = payload.get("text")
        prompt_audio_path = payload.get("prompt_audio_path") or payload.get("spk_audio_prompt")
        output_path = payload.get("output_path")
        language = payload.get("language") or "zh"

        if not text:
            raise ValueError("text is required")
        if not prompt_audio_path:
            raise ValueError("prompt_audio_path is required for TTS voice-clone validation")
        if not output_path:
            output_dir = MODEL_DIR / "artifacts/outputs"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "indextts2_prediction.wav"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.model_loaded:
            self.load()
        assert self._model is not None

        self._model.infer(
            spk_audio_prompt=str(prompt_audio_path),
            text=text,
            output_path=str(output_path),
            verbose=False,
        )
        return PredictionResult(
            text=text,
            audio_path=str(output_path),
            language=language,
            raw={"model_path": str(self.model_path)},
        )

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "loaded" if self.model_loaded else "ready",
            "model_loaded": self.model_loaded,
            "model_path": str(self.model_path),
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
    source = payload.get("source") or {}
    source_id = source.get("id")
    if source_id:
        candidate = Path(source_id)
        if not candidate.is_absolute():
            candidate = MODEL_DIR.parents[3] / candidate
        if candidate.exists():
            return str(candidate)
    provider = payload.get("provider_cache_path")
    if provider:
        provider_path = Path(provider)
        marker = ".runtime/modelscope_cache/"
        provider_text = str(provider_path)
        if marker in provider_text:
            candidate = MODEL_DIR / ".runtime/modelscope_cache" / provider_text.split(marker, 1)[1]
            if candidate.exists():
                return str(candidate)
    return None
