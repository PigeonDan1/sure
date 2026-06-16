"""SURE wrapper for Plachtaa/seed-vc V2 voice conversion."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import soundfile as sf


MODEL_DIR = Path(__file__).resolve().parent
SOURCE_DIR = MODEL_DIR / ".runtime/source/seed-vc"


@dataclass
class PredictionResult:
    audio_path: str = ""
    source_audio_path: str = ""
    reference_audio_path: str = ""
    task: str = "VC"
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelWrapper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.source_dir = Path(self.config.get("source_dir") or SOURCE_DIR)
        self.device_name = self.config.get("device") or os.environ.get("DEVICE") or "cuda"
        self.diffusion_steps = int(self.config.get("diffusion_steps", 10))
        self.length_adjust = float(self.config.get("length_adjust", 1.0))
        self.intelligibility_cfg_rate = float(self.config.get("intelligibility_cfg_rate", 0.7))
        self.similarity_cfg_rate = float(self.config.get("similarity_cfg_rate", 0.7))
        self.convert_style = bool(self.config.get("convert_style", False))
        self.model_loaded = False
        self._inference_v2 = None
        self._torch = None

    def load(self) -> None:
        if not self.source_dir.exists():
            raise FileNotFoundError(f"Missing Seed-VC source checkout: {self.source_dir}")

        os.environ.setdefault("HF_HOME", str((MODEL_DIR / ".runtime/huggingface").resolve()))
        os.environ.setdefault("HF_HUB_CACHE", str((MODEL_DIR / ".runtime/huggingface/hub").resolve()))
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        os.environ.setdefault("XDG_CACHE_HOME", str((MODEL_DIR / ".runtime/cache").resolve()))
        os.environ.setdefault("MPLCONFIGDIR", str((MODEL_DIR / ".runtime/matplotlib").resolve()))

        sys.path.insert(0, str(self.source_dir))
        import torch
        import inference_v2

        device = _resolve_device(torch, self.device_name)
        inference_v2.device = device
        inference_v2.dtype = torch.float16 if device.type == "cuda" else torch.float32
        inference_v2.vc_wrapper_v2 = None

        args = SimpleNamespace(
            ar_checkpoint_path=self.config.get("ar_checkpoint_path"),
            cfm_checkpoint_path=self.config.get("cfm_checkpoint_path"),
            compile=bool(self.config.get("compile", False)),
        )
        with _pushd(self.source_dir):
            inference_v2.vc_wrapper_v2 = inference_v2.load_v2_models(args)

        self._torch = torch
        self._inference_v2 = inference_v2
        self.model_loaded = True

    def predict(self, input_data: Any) -> PredictionResult:
        payload = input_data if isinstance(input_data, dict) else {"source_audio_path": input_data}
        source_audio_path = payload.get("source_audio_path") or payload.get("source")
        reference_audio_path = (
            payload.get("reference_audio_path")
            or payload.get("target_audio_path")
            or payload.get("target")
            or payload.get("ref_audio_path")
        )
        output_path = Path(payload.get("output_path") or MODEL_DIR / "artifacts/outputs/seed_vc_v2_smoke.wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not source_audio_path:
            raise ValueError("source_audio_path is required for VC inference")
        if not reference_audio_path:
            raise ValueError("reference_audio_path is required for VC inference")
        if not self.model_loaded:
            self.load()
        assert self._inference_v2 is not None

        args = SimpleNamespace(
            diffusion_steps=int(payload.get("diffusion_steps", self.diffusion_steps)),
            length_adjust=float(payload.get("length_adjust", self.length_adjust)),
            intelligibility_cfg_rate=float(payload.get("intelligibility_cfg_rate", self.intelligibility_cfg_rate)),
            similarity_cfg_rate=float(payload.get("similarity_cfg_rate", self.similarity_cfg_rate)),
            top_p=float(payload.get("top_p", 0.9)),
            temperature=float(payload.get("temperature", 1.0)),
            repetition_penalty=float(payload.get("repetition_penalty", 1.0)),
            convert_style=bool(payload.get("convert_style", self.convert_style)),
            anonymization_only=bool(payload.get("anonymization_only", False)),
            ar_checkpoint_path=self.config.get("ar_checkpoint_path"),
            cfm_checkpoint_path=self.config.get("cfm_checkpoint_path"),
            compile=bool(self.config.get("compile", False)),
        )
        with _pushd(self.source_dir):
            converted_audio = self._inference_v2.convert_voice_v2(
                str(source_audio_path),
                str(reference_audio_path),
                args,
            )
        if converted_audio is None:
            raise RuntimeError("Seed-VC returned no converted audio")

        sample_rate, audio = converted_audio
        sf.write(output_path, audio, sample_rate)
        return PredictionResult(
            audio_path=str(output_path),
            source_audio_path=str(source_audio_path),
            reference_audio_path=str(reference_audio_path),
            raw={
                "sample_rate": int(sample_rate),
                "num_samples": int(len(audio)),
                "device": self.healthcheck()["device"],
                "source_dir": str(self.source_dir),
            },
        )

    def healthcheck(self) -> dict[str, Any]:
        device = self.device_name
        if self._inference_v2 is not None:
            device = str(self._inference_v2.device)
        return {
            "status": "loaded" if self.model_loaded else "ready",
            "model_loaded": self.model_loaded,
            "source_dir": str(self.source_dir),
            "device": device,
        }


def _resolve_device(torch: Any, requested: str):
    if requested.startswith("cuda") and torch.cuda.is_available():
        return torch.device(requested)
    if requested == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


@contextmanager
def _pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
