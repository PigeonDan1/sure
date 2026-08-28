#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".ogg", ".m4a"}


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_fixture(resolved: dict) -> Path:
    explicit = resolved.get("fixture_path")
    if explicit:
        path = Path(str(explicit))
        if path.is_file():
            return path
        raise ValueError(f"fixture must be a file: {path}")
    build_context = Path(str(resolved["build_context"]))
    preferred = [
        build_context / "examples" / "smoke.wav",
        build_context / "examples" / "smoke.flac",
        build_context / "smoke.wav",
    ]
    for candidate in preferred:
        if candidate.is_file():
            return candidate
    examples = build_context / "examples"
    matches = sorted(path for path in examples.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES) if examples.is_dir() else []
    if len(matches) == 1:
        return matches[0]
    raise ValueError("fixture could not be selected unambiguously; pass fixture=/absolute/audio/path")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    source = choose_fixture(resolved).resolve()
    staged_dir = run_dir / "fixture"
    staged_dir.mkdir(parents=True, exist_ok=True)
    destination = staged_dir / source.name
    shutil.copy2(source, destination)
    payload = {
        "schema": "sure.trans.fixture_manifest.v1",
        "status": "ready",
        "model_name": resolved["model_name"],
        "task_type": resolved["task_type"],
        "source_path": str(source),
        "staged_path": str(destination),
        "sha256": sha256(destination),
        "size_bytes": destination.stat().st_size,
        "sample_count": 1,
        "link_policy": "copy",
    }
    output = artifacts / "fixture_manifest.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
