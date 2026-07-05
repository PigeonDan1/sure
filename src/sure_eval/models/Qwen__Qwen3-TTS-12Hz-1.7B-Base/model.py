"""Qwen3-TTS 1.7B Base wrapper for SURE-EVAL.

Responsibilities:
- Resolve model-local Hugging Face or ModelScope checkpoints.
- Load qwen_tts.Qwen3TTSModel lazily.
- Provide voice-clone TTS inference with a reference audio fixture.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"


class ModelLoadError(RuntimeError):
    """Raised when the Qwen3-TTS runtime cannot be loaded."""


class InferenceError(RuntimeError):
    """Raised when voice-clone generation fails."""


@dataclass
class TTSPredictionResult:
    wavs: Any
    sample_rate: int
    text: str
    language: str
    ref_audio: str
    x_vector_only_mode: bool
    audio_path: str | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.wavs is not None:
            payload["wavs_summary"] = _summarize_wavs(self.wavs)
            payload["wavs"] = payload["wavs_summary"]
        return payload


def _summarize_wavs(wavs: Any) -> dict[str, Any]:
    if hasattr(wavs, "shape"):
        return {"type": type(wavs).__name__, "shape": list(wavs.shape)}
    if isinstance(wavs, (list, tuple)):
        first = wavs[0] if wavs else None
        first_shape = list(first.shape) if hasattr(first, "shape") else None
        return {"type": type(wavs).__name__, "length": len(wavs), "first_shape": first_shape}
    return {"type": type(wavs).__name__, "repr": repr(wavs)[:200]}


class ModelWrapper:
    """Model-local wrapper around qwen_tts.Qwen3TTSModel.generate_voice_clone."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.model_path = self.config.get("model_path") or os.environ.get("MODEL_PATH")
        self.device_map = self.config.get("device_map") or os.environ.get("DEVICE_MAP") or "cuda:0"
        self.dtype_name = self.config.get("dtype") or os.environ.get("TORCH_DTYPE") or "bfloat16"
        self.attn_implementation = (
            self.config.get("attn_implementation")
            or os.environ.get("ATTN_IMPLEMENTATION")
            or "eager"
        )
        self._model = None
        self.model_loaded = False

    def _resolve_model_path(self) -> str:
        if self.model_path:
            candidate = Path(self.model_path)
            if candidate.exists():
                return str(candidate.resolve())
            return self.model_path

        manifest_path = MODEL_DIR / "artifacts" / "weights_manifest.json"
        if manifest_path.exists():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            resolved = payload.get("resolved_local_model_path")
            if resolved and Path(resolved).exists():
                return str(Path(resolved).resolve())

        checkpoint_path = MODEL_DIR / "checkpoints" / "Qwen3-TTS-12Hz-1.7B-Base"
        if checkpoint_path.exists():
            return str(checkpoint_path.resolve())

        hf_repo_dir = MODEL_DIR / ".runtime" / "huggingface" / "hub" / "models--Qwen--Qwen3-TTS-12Hz-1.7B-Base"
        snapshots_dir = hf_repo_dir / "snapshots"
        if snapshots_dir.exists():
            snapshots = sorted(
                (path for path in snapshots_dir.iterdir() if path.is_dir()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if snapshots:
                return str(snapshots[0].resolve())

        modelscope_path = MODEL_DIR / ".runtime" / "modelscope_cache" / "Qwen" / "Qwen3-TTS-12Hz-1.7B-Base"
        if modelscope_path.exists():
            return str(modelscope_path.resolve())

        return DEFAULT_MODEL_ID

    def _resolve_dtype(self):
        import torch

        mapping = {
            "bfloat16": torch.bfloat16,
            "bf16": torch.bfloat16,
            "float16": torch.float16,
            "fp16": torch.float16,
            "float32": torch.float32,
            "fp32": torch.float32,
        }
        try:
            return mapping[self.dtype_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported dtype: {self.dtype_name}") from exc

    def load(self) -> None:
        if self.model_loaded:
            return

        runtime = MODEL_DIR / ".runtime"
        os.environ.setdefault("HF_HOME", str((runtime / "huggingface").resolve()))
        os.environ.setdefault("HF_HUB_CACHE", str((runtime / "huggingface" / "hub").resolve()))
        os.environ.setdefault("MODELSCOPE_CACHE", str((runtime / "modelscope_cache").resolve()))
        os.environ.setdefault("MPLCONFIGDIR", str((runtime / "matplotlib").resolve()))
        os.environ.setdefault("TMPDIR", str((runtime / "tmp").resolve()))
        for directory in ("huggingface", "huggingface/hub", "modelscope_cache", "matplotlib", "tmp"):
            (runtime / directory).mkdir(parents=True, exist_ok=True)

        try:
            import torch
            from qwen_tts import Qwen3TTSModel
        except Exception as exc:  # noqa: BLE001 - preserve dependency error.
            raise ModelLoadError(f"Qwen3-TTS dependencies are not importable: {exc}") from exc

        if str(self.device_map).startswith("cuda") and not torch.cuda.is_available():
            raise ModelLoadError(
                "CUDA is required for the configured Base load path "
                f"(device_map={self.device_map}, attn_implementation={self.attn_implementation}) "
                "but torch.cuda.is_available() is false."
            )

        try:
            self._model = Qwen3TTSModel.from_pretrained(
                self._resolve_model_path(),
                device_map=self.device_map,
                dtype=self._resolve_dtype(),
                attn_implementation=self.attn_implementation,
            )
        except Exception as exc:  # noqa: BLE001
            raise ModelLoadError(f"Qwen3TTSModel.from_pretrained failed: {exc}") from exc
        self.model_loaded = True

    def predict(self, input_data: Any) -> TTSPredictionResult:
        payload = input_data if isinstance(input_data, dict) else {"text": str(input_data)}
        text = payload.get("text")
        language = payload.get("language") or "English"
        ref_audio = payload.get("ref_audio") or payload.get("reference_audio_path") or payload.get("prompt_audio_path")
        x_vector_only_mode = bool(payload.get("x_vector_only_mode", True))
        max_new_tokens = int(payload.get("max_new_tokens", 128))
        output_path = payload.get("output_path")
        if not text:
            raise ValueError("text is required")
        if not ref_audio:
            raise ValueError("ref_audio/reference_audio_path is required")

        if not self.model_loaded:
            self.load()
        assert self._model is not None

        try:
            wavs, sample_rate = self._model.generate_voice_clone(
                text=text,
                language=language,
                ref_audio=str(ref_audio),
                x_vector_only_mode=x_vector_only_mode,
                max_new_tokens=max_new_tokens,
            )
            if output_path:
                import soundfile as sf

                output = Path(output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                first_wav = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
                sf.write(str(output), first_wav, int(sample_rate))
                output_path = str(output)
        except Exception as exc:  # noqa: BLE001
            raise InferenceError(f"generate_voice_clone failed: {exc}") from exc

        return TTSPredictionResult(
            wavs=wavs,
            sample_rate=int(sample_rate),
            text=str(text),
            language=str(language),
            ref_audio=str(ref_audio),
            x_vector_only_mode=x_vector_only_mode,
            audio_path=output_path,
            raw={"model_path": self._resolve_model_path(), "max_new_tokens": max_new_tokens},
        )

    def healthcheck(self) -> dict[str, Any]:
        return {
            "status": "loaded" if self.model_loaded else "ready",
            "model_loaded": self.model_loaded,
            "model_path": self._resolve_model_path(),
            "device_map": self.device_map,
            "dtype": self.dtype_name,
            "attn_implementation": self.attn_implementation,
        }
