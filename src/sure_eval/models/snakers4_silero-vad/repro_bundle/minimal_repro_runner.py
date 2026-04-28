#!/usr/bin/env python3

import argparse
import importlib.metadata
import json
from importlib import resources
from pathlib import Path


def build_output(audio_path: str) -> dict:
    from silero_vad import get_speech_timestamps, load_silero_vad, read_audio

    model = load_silero_vad(onnx=False)
    model.eval()
    wav = read_audio(audio_path, sampling_rate=16000)
    segments = get_speech_timestamps(
        wav,
        model,
        sampling_rate=16000,
        return_seconds=True,
    )
    package_path = resources.files("silero_vad")
    model_file_path = resources.files("silero_vad.data").joinpath("silero_vad.jit")
    return {
        "segments": segments,
        "sample_rate": 16000,
        "audio_path": audio_path,
        "audio_duration_sec": float(len(wav) / 16000),
        "model_backend": "silero_vad_pytorch_jit",
        "error_code": None,
        "package_version": importlib.metadata.version("silero-vad"),
        "package_path": str(package_path),
        "model_file_path": str(model_file_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = build_output(str(Path(args.audio).resolve()))
    output_path = Path(args.output).resolve()
    output_path.write_text(json.dumps(payload, indent=2))
    print(json.dumps({"status": "ok", "output_path": str(output_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
