"""Minimal SURE wrapper for SpeechBrain MetricGAN+ VoiceBank (16 kHz SE)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4


class ModelWrapper:
    def __init__(self, config=None):
        self.config = config or {}
        self.model = None
        self.model_dir = Path(__file__).resolve().parent

    def load(self) -> None:
        from speechbrain.inference.enhancement import SpectralMaskEnhancement

        cache_dir = Path(
            os.environ.get("SURE_SE_MODEL_CACHE") or self.model_dir / ".runtime" / "weights"
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.model = SpectralMaskEnhancement.from_hparams(
            source="speechbrain/metricgan-plus-voicebank",
            savedir=str(cache_dir),
            run_opts={"device": os.environ.get("SURE_SE_DEVICE", "cpu")},
        )

    def predict(self, input_data):
        import torch
        import torchaudio

        if not isinstance(input_data, dict):
            raise ValueError("SE input must be an object")
        source = input_data.get("audio_path") or input_data.get("noisy_audio_path")
        if not isinstance(source, str) or not source:
            raise ValueError("audio_path is required")
        if input_data.get("audio_path") and input_data.get("noisy_audio_path") and input_data["audio_path"] != input_data["noisy_audio_path"]:
            raise ValueError("audio_path and noisy_audio_path must refer to the same input")
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        target = input_data.get("output_path")
        output_path = (
            Path(target).expanduser().resolve()
            if isinstance(target, str) and target
            else Path(os.environ.get("SURE_SE_OUTPUT_DIR") or tempfile.gettempdir()) / "sure-se" / f"{uuid4().hex}.wav"
        )
        if output_path.suffix.lower() != ".wav":
            raise ValueError("output_path must be a WAV file")
        if output_path == source_path:
            raise ValueError("output_path must not overwrite the noisy input")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.model is None:
            self.load()
        waveform, sample_rate = torchaudio.load(str(source_path))
        waveform = waveform.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            waveform = torchaudio.functional.resample(waveform, sample_rate, 16000)
        with torch.no_grad():
            enhanced = self.model.enhance_batch(
                waveform, lengths=torch.tensor([1.0])
            ).detach().cpu().reshape(1, -1)
        if not enhanced.numel() or not torch.isfinite(enhanced).all():
            raise RuntimeError("SE model produced empty or non-finite audio")
        torchaudio.save(
            str(output_path), enhanced, 16000, encoding="PCM_S", bits_per_sample=16
        )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("SE model did not write its WAV output")
        return {"audio_path": str(output_path)}

    def healthcheck(self):
        return {"status": "ready" if self.model is not None else "loading", "model_loaded": self.model is not None}
