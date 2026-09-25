"""SURE adapter for the single-source SepFormer WHAM16k enhancement model."""
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

    def load(self):
        from speechbrain.inference.separation import SepformerSeparation

        cache = Path(os.environ.get("SURE_SE_MODEL_CACHE") or self.model_dir / ".runtime/weights")
        cache.mkdir(parents=True, exist_ok=True)
        self.model = SepformerSeparation.from_hparams(
            source="speechbrain/sepformer-wham16k-enhancement",
            revision="90b3c5c3ffe3e04387b566715ab5fff36ec7b9d9",
            savedir=str(cache),
            run_opts={"device": os.environ.get("SURE_SE_DEVICE", "cpu")},
        )
        if self.model.hparams.num_spks != 1 or self.model.hparams.sample_rate != 16000:
            raise ValueError("Expected the single-source 16 kHz enhancement checkpoint")

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
        waveform, sample_rate = torchaudio.load(str(source_path))
        waveform = waveform.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            waveform = torchaudio.functional.resample(waveform, sample_rate, 16000)
        if not waveform.numel() or not torch.isfinite(waveform).all():
            raise ValueError("Expected nonempty finite noisy audio")
        if self.model is None:
            self.load()
        with torch.no_grad():
            sources = self.model.separate_batch(waveform).detach().cpu()
        if sources.ndim != 3 or sources.shape[0] != 1 or sources.shape[2] != 1:
            raise ValueError("Expected exactly one enhanced source")
        enhanced = sources[:, :, 0]
        if not enhanced.numel() or not torch.isfinite(enhanced).all():
            raise RuntimeError("SE model produced empty or non-finite audio")
        # Match SpeechBrain separate_file peak normalization, with a silence guard.
        peak = enhanced.abs().max()
        if peak > 0:
            enhanced = enhanced / peak
        output_path.parent.mkdir(parents=True, exist_ok=True)
        torchaudio.save(str(output_path), enhanced, 16000, encoding="PCM_S", bits_per_sample=16)
        return {"audio_path": str(output_path)}

    def healthcheck(self):
        return {"status": "ready" if self.model is not None else "loading", "model_loaded": self.model is not None}
