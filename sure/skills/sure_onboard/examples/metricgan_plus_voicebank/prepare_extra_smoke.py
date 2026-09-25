"""Derive three additional SE cases from existing, attributed LibriSpeech fixtures.

Run with the model Python (NumPy, Torch, Torchaudio). These synthetic mixtures
exercise transport/format support, not the model's standard quality benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
import torchaudio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[5]
    source_dir = repo / "fixtures/tasks/asr/qwen3_asr_smoke/asr_en"
    sources = sorted(source_dir.glob("sample_*.wav"))
    if len(sources) != 3:
        raise ValueError("Expected the three attributed English ASR smoke fixtures")
    rows, transforms = [], []
    for index, (source, snr_db, rate, channels) in enumerate(zip(sources, (0, 5, 10), (16000, 8000, 16000), (1, 1, 2))):
        clean, source_rate = torchaudio.load(str(source))
        clean = clean.mean(dim=0, keepdim=True)
        if source_rate != rate:
            clean = torchaudio.functional.resample(clean, source_rate, rate)
        seed = 20260925 + index
        noise = torch.from_numpy(np.random.default_rng(seed).standard_normal(clean.shape).astype(np.float32))
        noise *= clean.square().mean().sqrt() / noise.square().mean().sqrt() / (10 ** (snr_db / 20))
        noisy = clean + noise
        if channels == 2:
            noisy = torch.cat((noisy * 0.75, noisy * 1.25), dim=0)
        gain = 0.9 / max(float(noisy.abs().max()), float(clean.abs().max()))
        clean, noisy = clean * gain, noisy * gain
        key = f"extra_{source.stem}_{rate}hz_{channels}ch"
        reference_path, noisy_path = output / f"{key}_clean.wav", output / f"{key}_noisy.wav"
        for path, audio in ((reference_path, clean), (noisy_path, noisy)):
            torchaudio.save(str(path), audio, rate, encoding="PCM_S", bits_per_sample=16)
        rows.append({"key": key, "audio": noisy_path.name, "reference_audio": reference_path.name,
                     "task": "SE", "language": "en"})
        transforms.append({"key": key, "source": str(source.relative_to(repo)),
                           "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                           "noise": "NumPy default_rng Gaussian", "seed": seed, "snr_db": snr_db,
                           "sample_rate": rate, "channels": channels, "shared_peak_gain": gain,
                           "noisy_sha256": hashlib.sha256(noisy_path.read_bytes()).hexdigest(),
                           "reference_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest()})
    (output / "gt.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (output / "provenance.json").write_text(json.dumps({
        "scope": "synthetic development smoke; not VoiceBank/DEMAND evaluation",
        "source_provenance": str((source_dir / "provenance.json").relative_to(repo)),
        "source_attribution": json.loads((source_dir / "provenance.json").read_text()),
        "transforms": transforms,
    }, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
