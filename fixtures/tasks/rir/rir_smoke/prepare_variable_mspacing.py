#!/usr/bin/env python3
"""Materialize a bounded VariableMSpacingTest samples_jsonl selection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prediction-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--branch",
        choices=("single_channel", "multi_channel"),
        default="multi_channel",
        help="single_channel selects reference channel 0; multi_channel keeps both channels",
    )
    parser.add_argument(
        "--reference-channel-root",
        type=Path,
        help="staging directory for channel-0 .npy references in the single-channel branch",
    )
    parser.add_argument(
        "--input-mode",
        choices=("native", "mono_channel0", "duplicate_channel0"),
        default="native",
        help="optional input transform for single-channel model adapters",
    )
    parser.add_argument(
        "--input-staging-root",
        type=Path,
        help="staging directory for transformed model input WAVs",
    )
    parser.add_argument(
        "--allow-missing-predictions",
        action="store_true",
        help="write rows before model inference has produced every RIR",
    )
    args = parser.parse_args()
    rows = _read_rows(args.manifest)
    selected = _select_rows(rows)
    if args.branch == "single_channel" and args.reference_channel_root is None:
        parser.error("--reference-channel-root is required for --branch single_channel")
    if args.branch == "single_channel" and args.input_mode == "native":
        parser.error(
            "--input-mode must be mono_channel0 or duplicate_channel0 for --branch single_channel"
        )
    if args.reference_channel_root is not None:
        args.reference_channel_root.mkdir(parents=True, exist_ok=True)
    if args.input_mode != "native" and args.input_staging_root is None:
        parser.error("--input-staging-root is required when --input-mode is not native")
    if args.input_staging_root is not None:
        args.input_staging_root.mkdir(parents=True, exist_ok=True)
    output_rows = []
    for row in selected:
        sample_id = str(row["sample_id"])
        reference = Path(str(row["rir"])).expanduser().resolve()
        if args.branch == "single_channel":
            reference = _stage_first_channel(reference, args.reference_channel_root / f"{sample_id}.npy")
        prediction = (args.prediction_root / f"{sample_id}.wav").resolve()
        if not reference.is_file():
            raise FileNotFoundError(f"reference RIR not found for {sample_id}: {reference}")
        if not args.allow_missing_predictions and not prediction.is_file():
            raise FileNotFoundError(f"prediction RIR not found for {sample_id}: {prediction}")
        input_audio = Path(str(row["mixture"])).expanduser().resolve()
        if args.input_mode != "native":
            input_audio = _stage_input(
                input_audio,
                args.input_staging_root / f"{sample_id}.wav",
                mode=args.input_mode,
            )
        output_rows.append(
            {
                "sample_id": sample_id,
                "input_audio": str(input_audio),
                "prediction_rir": str(prediction),
                "reference_rir": str(reference),
                "sample_rate": int(row.get("sr", 16000)),
                "channel_layout": "mono" if args.branch == "single_channel" else "binaural_lr",
                "metadata": {
                    "microphone_spacing_m": float(row["mic_spacing"]),
                    "scene_id": str(row["scene_id"]),
                    "microphone_spacing_cm": int(row["mic_spacing_cm"]),
                },
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(output_rows)} RIR rows to {args.output}")
    return 0


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if not rows:
        raise ValueError(f"manifest contains no rows: {path}")
    return rows


def _select_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    spacings = list(range(3, 31, 3))
    for spacing in spacings:
        matches = [row for row in rows if int(row.get("mic_spacing_cm", -1)) == spacing]
        if len(matches) < 2:
            raise ValueError(f"spacing {spacing} cm has fewer than two manifest rows")
        selected.extend(matches[:2])
    if len(selected) != 20:
        raise AssertionError(f"expected 20 selected rows, got {len(selected)}")
    sample_ids = [str(row.get("sample_id", "")) for row in selected]
    if any(not sample_id for sample_id in sample_ids) or len(set(sample_ids)) != len(sample_ids):
        raise ValueError("selected VariableMSpacingTest rows must have unique sample_id values")
    return selected


def _stage_first_channel(source: Path, destination: Path) -> Path:
    array = np.asarray(np.load(source), dtype="float64")
    if array.ndim != 2:
        raise ValueError(f"expected a two-channel RIR array: {source}")
    if array.shape[0] == 2:
        first_channel = array[0]
    elif array.shape[1] == 2:
        first_channel = array[:, 0]
    else:
        raise ValueError(f"expected a two-channel RIR array: {source}")
    np.save(destination, first_channel)
    return destination.resolve()


def _stage_input(source: Path, destination: Path, *, mode: str) -> Path:
    samples, rate = sf.read(source, always_2d=True, dtype="float32")
    if samples.size == 0 or not np.isfinite(samples).all():
        raise ValueError(f"input audio is empty or non-finite: {source}")
    channel = samples[:, :1]
    if mode == "mono_channel0":
        transformed = channel
    elif mode == "duplicate_channel0":
        transformed = np.repeat(channel, 2, axis=1)
    else:
        raise ValueError(f"unsupported input staging mode: {mode}")
    sf.write(destination, transformed, rate, subtype="FLOAT")
    return destination.resolve()


if __name__ == "__main__":
    raise SystemExit(main())
