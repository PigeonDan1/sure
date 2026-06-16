"""SURE wrapper for SWivid/F5-TTS_Emilia-ZH-EN."""

from __future__ import annotations

import json
import os
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
            or MODEL_DIR / ".runtime/modelscope_cache/SWivid/F5-TTS_Emilia-ZH-EN"
        )
        self.device = self.config.get("device") or os.environ.get("DEVICE") or "cuda:0"
        self.model_loaded = False
        self._model = None

    def load(self) -> None:
        os.environ.setdefault("MPLCONFIGDIR", str(MODEL_DIR / ".runtime/matplotlib"))
        os.environ.setdefault("HF_HOME", str(MODEL_DIR / ".runtime/huggingface"))
        os.environ.setdefault("HF_HUB_CACHE", str(MODEL_DIR / ".runtime/huggingface/hub"))

        from f5_tts.api import F5TTS

        ckpt_file = self.model_path / "model_1250000.safetensors"
        vocab_file = self.model_path / "vocab.txt"
        vocoder_path = MODEL_DIR / ".runtime/vocoder/vocos-mel-24khz"
        vocoder_config = vocoder_path / "config.yaml"
        vocoder_weights = vocoder_path / "pytorch_model.bin"
        if not ckpt_file.exists():
            raise FileNotFoundError(f"Missing F5-TTS checkpoint: {ckpt_file}")
        if not vocab_file.exists():
            raise FileNotFoundError(f"Missing F5-TTS vocab: {vocab_file}")
        if not vocoder_config.exists() or not vocoder_weights.exists():
            raise FileNotFoundError(
                "Missing F5-TTS vocoder files. Expected "
                f"{vocoder_config} and {vocoder_weights}. "
                "F5-TTS otherwise attempts an implicit HuggingFace download "
                "from charactr/vocos-mel-24khz, which is not reliable in the "
                "current local validation environment."
            )

        self._model = F5TTS(
            model="F5TTS_v1_Base",
            ckpt_file=str(ckpt_file),
            vocab_file=str(vocab_file),
            device=self.device,
            vocoder_local_path=str(vocoder_path),
            hf_cache_dir=str(MODEL_DIR / ".runtime/huggingface"),
        )
        self.model_loaded = True

    def predict(self, input_data: Any) -> PredictionResult:
        payload = input_data if isinstance(input_data, dict) else {"text": input_data}
        text = payload.get("text")
        prompt_audio_path = payload.get("prompt_audio_path") or payload.get("ref_audio_path")
        prompt_text = payload.get("prompt_text") or payload.get("ref_text") or ""
        output_path = payload.get("output_path")
        language = payload.get("language") or "zh"

        if not text:
            raise ValueError("text is required")
        if not prompt_audio_path:
            raise ValueError("prompt_audio_path is required for TTS voice-clone validation")
        if not output_path:
            output_dir = MODEL_DIR / "artifacts/outputs"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "f5_tts_prediction.wav"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.model_loaded:
            self.load()
        assert self._model is not None

        wav, sr, _ = self._model.infer(
            ref_file=str(prompt_audio_path),
            ref_text=prompt_text,
            gen_text=text,
            file_wave=str(output_path),
            show_info=lambda *_args, **_kwargs: None,
            progress=None,
            seed=int(payload.get("seed", 1234)),
            nfe_step=int(payload.get("nfe") or payload.get("nfe_step") or 32),
            sway_sampling_coef=float(payload.get("sway_sampling_coef", -1.0)),
            speed=float(payload.get("speed", 1.0)),
        )
        return PredictionResult(
            text=text,
            audio_path=str(output_path),
            language=language,
            raw={"sample_rate": sr, "num_samples": int(len(wav)), "model_path": str(self.model_path)},
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
