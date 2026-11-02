#!/usr/bin/env python3
"""Merge two independent mono model outputs into a binaural RIR WAV."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left-root", type=Path, required=True)
    parser.add_argument("--right-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("sample_ids", nargs="+", help="sample IDs whose <id>.wav files should be merged")
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    for sample_id in args.sample_ids:
        left, left_rate = _read_mono(args.left_root / f"{sample_id}.wav")
        right, right_rate = _read_mono(args.right_root / f"{sample_id}.wav")
        if left_rate != right_rate:
            raise ValueError(f"sample-rate mismatch for {sample_id}: {left_rate}, {right_rate}")
        length = min(len(left), len(right))
        if length == 0:
            raise ValueError(f"empty RIR output for {sample_id}")
        destination = args.output_root / f"{sample_id}.wav"
        sf.write(destination, np.column_stack((left[:length], right[:length])), left_rate, subtype="FLOAT")
    print(f"merged {len(args.sample_ids)} binaural RIR outputs in {args.output_root}")
    return 0


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    data, rate = sf.read(path, always_2d=True, dtype="float64")
    if data.shape[1] != 1:
        raise ValueError(f"expected one channel in {path}, got {data.shape[1]}")
    if data.size == 0 or not np.isfinite(data).all():
        raise ValueError(f"RIR output is empty or non-finite: {path}")
    return data[:, 0], int(rate)


if __name__ == "__main__":
    raise SystemExit(main())
