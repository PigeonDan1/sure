from __future__ import annotations

import os
import sys
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
            or os.environ.get("INDEXTTS2_MODEL_ROOT")
            or MODEL_DIR / ".runtime/modelscope_cache/IndexTeam/IndexTTS-2"
        ).resolve()
        self.source_path = Path(
            self.config.get("source_path")
            or os.environ.get("INDEXTTS2_SOURCE_ROOT")
            or MODEL_DIR / ".runtime/source/index-tts"
        ).resolve()
        self.device = self.config.get("device") or os.environ.get("DEVICE") or "cuda:0"
        self.model_loaded = False
        self._model = None

    def load(self) -> None:
        os.environ.setdefault("MPLCONFIGDIR", str(MODEL_DIR / ".runtime/matplotlib"))
        os.environ.setdefault("HF_HOME", str((MODEL_DIR / ".runtime/huggingface").resolve()))
        os.environ.setdefault("HF_HUB_CACHE", str((MODEL_DIR / ".runtime/huggingface/hub").resolve()))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("INDEXTTS2_WRAPPER_DIR", str(MODEL_DIR))

        required = [
            self.model_path / "config.yaml",
            self.model_path / "gpt.pth",
            self.model_path / "s2mel.pth",
            self.model_path / "bpe.model",
            self.model_path / "wav2vec2bert_stats.pt",
            self.model_path / "feat1.pt",
            self.model_path / "feat2.pt",
            self.model_path / "qwen0.6bemo4-merge/config.json",
        ]
        for path in required:
            if not path.exists():
                raise FileNotFoundError(f"Missing IndexTTS-2 runtime file: {path}")
        if not self.source_path.exists():
            raise FileNotFoundError(f"Missing IndexTTS-2 source path: {self.source_path}")
        sys.path.insert(0, str(self.source_path))

        from indextts.infer_v2 import IndexTTS2

        self._model = IndexTTS2(
            cfg_path=str(self.model_path / "config.yaml"),
            model_dir=str(self.model_path),
            device=self.device,
            use_fp16=False,
            use_cuda_kernel=False,
            use_deepspeed=False,
            use_accel=False,
            use_torch_compile=False,
        )
        self.model_loaded = True

    def predict(self, payload: dict[str, Any] | str) -> PredictionResult:
        data = payload if isinstance(payload, dict) else {"text": payload}
        text = data.get("text")
        prompt_audio_path = data.get("prompt_audio_path") or data.get("spk_audio_prompt")
        language = data.get("language") or "zh"
        if not text:
            raise ValueError("text is required")
        if not prompt_audio_path:
            raise ValueError("prompt_audio_path is required")

        output_path = Path(data.get("output_path") or MODEL_DIR / "artifacts/outputs/indextts2_reonboard_smoke.wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.resolve() == Path(prompt_audio_path).resolve():
            raise ValueError("output_path must not point to the prompt audio")

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
            raw={
                "model_path": str(self.model_path),
                "source_path": str(self.source_path),
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
            "source_path": str(self.source_path),
            "device": self.device,
        }
