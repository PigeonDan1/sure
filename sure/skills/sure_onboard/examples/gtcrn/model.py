"""GTCRN via sherpa-onnx; copy into a model-local bundle before onboarding."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf


class ModelWrapper:
    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self.checkpoint = Path(__file__).parent / str(config.get("checkpoint") or "checkpoints/gtcrn_simple.onnx")
        self.denoiser = None

    def load(self) -> None:
        if self.denoiser is not None:
            return
        if not self.checkpoint.is_file():
            raise FileNotFoundError(self.checkpoint)
        config = sherpa_onnx.OfflineSpeechDenoiserConfig(
            model=sherpa_onnx.OfflineSpeechDenoiserModelConfig(
                gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=str(self.checkpoint)),
                num_threads=1, provider="cpu", debug=False,
            ),
        )
        if not config.validate():
            raise ValueError("Invalid GTCRN denoiser configuration")
        self.denoiser = sherpa_onnx.OfflineSpeechDenoiser(config)

    def predict(self, input_data: dict) -> dict:
        source = Path(input_data["audio_path"]).resolve()
        output = Path(input_data["output_path"]).resolve()
        if source == output:
            raise ValueError("output_path must differ from the noisy input")
        samples, rate = sf.read(source, dtype="float32", always_2d=True)
        if rate != 16000 or samples.shape[1] != 1:
            raise ValueError("This GTCRN adapter expects mono 16000 Hz input")
        if not samples.size or not np.isfinite(samples).all():
            raise ValueError("Input audio must be nonempty and finite")
        self.load()
        denoised = self.denoiser(np.ascontiguousarray(samples[:, 0]), rate)
        enhanced = np.asarray(denoised.samples, dtype=np.float32)
        if not enhanced.size or not np.isfinite(enhanced).all() or denoised.sample_rate != 16000:
            raise ValueError("Invalid enhanced audio")
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, enhanced, denoised.sample_rate, subtype="PCM_16")
        return {"audio_path": str(output), "enhanced_audio": str(output), "sample_rate": 16000,
                "duration_ms": 1000 * enhanced.size / 16000}

    def healthcheck(self) -> dict:
        self.load()
        return {"status": "ready", "task": "SE", "device": "cpu"}
