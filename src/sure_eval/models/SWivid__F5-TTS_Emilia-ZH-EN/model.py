from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent


@dataclass
class PredictionResult:
    text: str
    audio_path: str
    language: str = "zh"
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.model_path = Path(
            self.config.get("model_path")
            or os.environ.get("F5_TTS_MODEL_ROOT")
            or MODEL_DIR / ".runtime/modelscope_cache/SWivid/F5-TTS_Emilia-ZH-EN"
        ).resolve()
        self.vocoder_path = Path(
            self.config.get("vocoder_path")
            or os.environ.get("F5_TTS_VOCODER_ROOT")
            or MODEL_DIR / ".runtime/vocoder/vocos-mel-24khz"
        ).resolve()
        self.device = self.config.get("device") or os.environ.get("DEVICE") or "cuda:0"
        self.model_loaded = False
        self._model = None

    def load(self) -> None:
        os.environ.setdefault("MPLCONFIGDIR", str(MODEL_DIR / ".runtime/matplotlib"))
        os.environ.setdefault("HF_HOME", str(MODEL_DIR / ".runtime/huggingface"))
        os.environ.setdefault("HF_HUB_CACHE", str(MODEL_DIR / ".runtime/huggingface/hub"))

        ckpt_file = self.model_path / "model_1250000.safetensors"
        vocab_file = self.model_path / "vocab.txt"
        vocoder_config = self.vocoder_path / "config.yaml"
        vocoder_weights = self.vocoder_path / "pytorch_model.bin"
        for required in (ckpt_file, vocab_file, vocoder_config, vocoder_weights):
            if not required.exists():
                raise FileNotFoundError(f"Missing F5-TTS runtime file: {required}")

        from f5_tts.api import F5TTS

        self._model = F5TTS(
            model="F5TTS_v1_Base",
            ckpt_file=str(ckpt_file),
            vocab_file=str(vocab_file),
            device=self.device,
            vocoder_local_path=str(self.vocoder_path),
            hf_cache_dir=str(MODEL_DIR / ".runtime/huggingface"),
        )
        self.model_loaded = True

    def predict(self, payload: dict[str, Any] | str) -> PredictionResult:
        data = payload if isinstance(payload, dict) else {"text": payload}
        text = data.get("text")
        prompt_audio_path = data.get("prompt_audio_path") or data.get("ref_audio_path")
        prompt_text = data.get("prompt_text") or data.get("ref_text") or ""
        language = data.get("language") or "zh"
        if not text:
            raise ValueError("text is required")
        if not prompt_audio_path:
            raise ValueError("prompt_audio_path is required")

        output_path = Path(data.get("output_path") or MODEL_DIR / "artifacts/outputs/f5_tts_reonboard_smoke.wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.resolve() == Path(prompt_audio_path).resolve():
            raise ValueError("output_path must not point to the prompt audio")

        if not self.model_loaded:
            self.load()
        assert self._model is not None

        wav, sample_rate, _ = self._model.infer(
            ref_file=str(prompt_audio_path),
            ref_text=prompt_text,
            gen_text=text,
            file_wave=str(output_path),
            show_info=lambda *_args, **_kwargs: None,
            progress=None,
            seed=int(data.get("seed", 1234)),
            nfe_step=int(data.get("nfe_step", data.get("nfe", 32))),
            sway_sampling_coef=float(data.get("sway_sampling_coef", -1.0)),
            speed=float(data.get("speed", 1.0)),
        )
        return PredictionResult(
            text=text,
            audio_path=str(output_path),
            language=language,
            raw={
                "sample_rate": int(sample_rate),
                "num_samples": int(len(wav)),
                "model_path": str(self.model_path),
                "vocoder_path": str(self.vocoder_path),
                "device": self.device,
                "prompt_audio_path": str(prompt_audio_path),
            },
        )

    def health(self) -> dict[str, Any]:
        return self.healthcheck()

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "loaded" if self.model_loaded else "ready",
            "model_loaded": self.model_loaded,
            "model_path": str(self.model_path),
            "vocoder_path": str(self.vocoder_path),
            "device": self.device,
        }
