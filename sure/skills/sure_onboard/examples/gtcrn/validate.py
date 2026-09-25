"""Run with Model Python, not Harness Python."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from model import ModelWrapper


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    model = ModelWrapper()
    model.load()
    identity = id(model.denoiser)
    results = []
    for line in (args.fixture / "gt.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        source = args.fixture / row["noisy_audio"]
        start = time.perf_counter()
        result = model.predict({"audio_path": str(source), "output_path": str(args.output_dir / f"{row['key']}.wav")})
        elapsed = time.perf_counter() - start
        audio, rate = sf.read(result["audio_path"], dtype="float32")
        noisy, _ = sf.read(source, dtype="float32")
        # The upstream STFT/iSTFT returns whole hops, not the exact input
        # length. Keep that audio unchanged and record the native length.
        assert rate == 16000 and 0 < len(audio) and abs(len(audio) - len(noisy)) < 512
        assert np.isfinite(audio).all()
        assert not np.array_equal(noisy, audio), "Enhancement must not be an input passthrough"
        assert id(model.denoiser) == identity, "The model was reloaded between samples"
        results.append({"key": row["key"], **result, "elapsed_seconds": elapsed,
                        "input_frames": len(noisy), "output_frames": len(audio),
                        "length_policy": "native upstream STFT/iSTFT; no adapter padding, trimming, or alignment",
                        "rtf": elapsed / (len(audio) / rate),
                        "sha256": hashlib.sha256(Path(result["audio_path"]).read_bytes()).hexdigest()})
    report = {"status": "passed", "import": True, "load": True, "infer": True, "contract": True, "samples": results}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
