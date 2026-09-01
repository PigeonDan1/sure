#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".ogg", ".m4a"}
ANNOTATION_FIELDS = ("ground_truth", "target_text", "text", "segments", "label", "intent")


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


def has_annotation_value(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return value is not None


def clear_directory(path: Path, controlled_root: Path) -> None:
    if controlled_root.is_symlink() or path.is_symlink():
        raise ValueError(f"fixture staging directory must not be a symlink: {path}")
    resolved = path.resolve()
    root = controlled_root.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError(f"fixture staging directory must stay below {root}: {path}")
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            raise ValueError(f"fixture staging contains unsupported entry: {child}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    source = choose_fixture(resolved).resolve()
    task = str(resolved["task_type"]).replace("-", "_").lower()
    staged_dir = run_dir / "fixture" / task
    clear_directory(staged_dir, run_dir / "fixture")
    destination = staged_dir / source.name
    shutil.copy2(source, destination)
    expected_source = source.with_suffix(".expected.json")
    if not expected_source.is_file():
        raise ValueError(
            f"fixture reference annotation is missing: {expected_source}; "
            "provide a same-stem .expected.json instead of deriving ground truth from model output"
        )
    expected = read_object(expected_source)
    annotations = {
        field: expected[field]
        for field in ANNOTATION_FIELDS
        if field in expected and has_annotation_value(expected[field])
    }
    if not annotations:
        raise ValueError(
            f"fixture reference annotation has no non-empty supported field: {expected_source}"
        )
    # prompt_text is a TTS input, not a label: the fixture gate recomputes
    # annotation_fields from ANNOTATION_FIELDS and compares it with what the
    # sample declares, so listing prompt_text there fails every TTS fixture.
    annotation_fields = list(annotations)
    gt_extras: dict[str, object] = {}
    if task == "tts":
        prompt_text = expected.get("prompt_text")
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            raise ValueError(f"TTS fixture annotation requires non-empty prompt_text: {expected_source}")
        gt_extras["prompt_text"] = prompt_text.strip()
    expected_destination = staged_dir / expected_source.name
    shutil.copy2(expected_source, expected_destination)
    gt_jsonl = staged_dir / "gt.jsonl"
    audio_field = "reference_audio" if task in {"tts", "vc"} else "audio"
    gt_row = {audio_field: source.name, "task_type": task, **annotations, **gt_extras}
    gt_jsonl.write_text(json.dumps(gt_row, ensure_ascii=False) + "\n", encoding="utf-8")
    payload = {
        "schema": "sure.trans.fixture_manifest.v1",
        "status": "ready",
        "model_id": resolved["model_name"],
        "model_name": resolved["model_name"],
        "model_dir": str(run_dir),
        "task_type": task,
        "source_dir": str(source.parent),
        "staged_dir": str(staged_dir),
        "gt_jsonl": str(gt_jsonl),
        "samples": [
            {
                "key": source.stem,
                "audio": source.name,
                "audio_path": str(destination),
                "annotation_fields": annotation_fields,
            }
        ],
        "source_path": str(source),
        "staged_path": str(destination),
        "sha256": sha256(destination),
        "gt_sha256": sha256(gt_jsonl),
        "expected_sha256": sha256(expected_destination),
        "size_bytes": destination.stat().st_size,
        "sample_count": 1,
        "link_policy": "copy",
        "annotation_source": {
            "type": "fixture_expected_sidecar",
            "source_path": str(expected_source),
            "staged_path": str(expected_destination),
            "fallback": False,
        },
    }
    output = artifacts / "fixture_manifest.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
