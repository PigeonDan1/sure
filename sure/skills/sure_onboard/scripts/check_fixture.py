#!/usr/bin/env python3
"""Gate script for PREPARE_FIXTURE."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_task(task: str) -> str:
    return task.replace("-", "_").lower()


def is_relative_child(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def fail(message: str) -> int:
    print(f"CHECK_FIXTURE failed: {message}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        return fail(f"fixture_manifest.json not found at {path}")
    try:
        data = load_json(path)
    except Exception as exc:  # noqa: BLE001
        return fail(f"fixture_manifest.json is not valid JSON: {exc}")

    if not isinstance(data, dict):
        return fail("fixture_manifest.json must be a JSON object")
    required = ["model_dir", "task_type", "staged_dir", "gt_jsonl", "samples", "sample_count"]
    missing = [field for field in required if field not in data]
    if missing:
        return fail("missing required fields: " + ", ".join(missing))

    model_dir = Path(str(data["model_dir"])).resolve()
    task = canonical_task(str(data["task_type"]))
    staged_dir = Path(str(data["staged_dir"])).resolve()
    gt_jsonl = Path(str(data["gt_jsonl"])).resolve()

    if not model_dir.exists():
        return fail(f"model_dir does not exist: {model_dir}")
    expected_root = model_dir / "fixture"
    if not is_relative_child(staged_dir, expected_root):
        return fail(f"staged_dir must be under model_dir/fixture: {staged_dir}")
    expected_task_root = expected_root / task
    if not is_relative_child(staged_dir, expected_task_root):
        return fail(f"staged_dir must be under model_dir/fixture/{task}: {staged_dir}")
    if not staged_dir.exists() or not staged_dir.is_dir():
        return fail(f"staged_dir does not exist or is not a directory: {staged_dir}")
    if not gt_jsonl.exists():
        return fail(f"gt_jsonl does not exist: {gt_jsonl}")
    if gt_jsonl.parent.resolve() != staged_dir.resolve():
        return fail("gt_jsonl must be located directly inside staged_dir")

    samples = data.get("samples")
    if not isinstance(samples, list):
        return fail("samples must be an array")
    if data.get("sample_count") != len(samples):
        return fail("sample_count must match len(samples)")
    if not (1 <= len(samples) <= 5):
        return fail("sample_count must be between 1 and 5")

    parsed_rows = []
    for line_no, line in enumerate(gt_jsonl.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            return fail(f"{gt_jsonl}:{line_no} is not valid JSON: {exc}")
        if not isinstance(row, dict):
            return fail(f"{gt_jsonl}:{line_no} must be a JSON object")
        audio = row.get("audio") or row.get("wav") or row.get("prompt_audio") or row.get("reference_audio")
        if not isinstance(audio, str) or not audio:
            return fail(f"{gt_jsonl}:{line_no} must contain a non-empty audio/wav field")
        audio_path = Path(audio)
        if audio_path.is_absolute() or ".." in audio_path.parts:
            return fail(f"{gt_jsonl}:{line_no} audio path must be relative and stay inside staged_dir")
        resolved_audio = (gt_jsonl.parent / audio_path).resolve()
        if not is_relative_child(resolved_audio, staged_dir):
            return fail(f"{gt_jsonl}:{line_no} audio path escapes staged_dir: {audio}")
        if not resolved_audio.exists():
            return fail(f"{gt_jsonl}:{line_no} referenced audio does not exist: {audio}")
        annotation_fields = [
            field
            for field in ("ground_truth", "target_text", "text", "segments", "label", "intent")
            if field in row
        ]
        if not annotation_fields:
            return fail(
                f"{gt_jsonl}:{line_no} must contain at least one annotation field "
                "(ground_truth, target_text, text, segments, label, or intent)"
            )
        if task == "sa_asr" and "segments" not in row:
            return fail(f"{gt_jsonl}:{line_no} task sa_asr requires speaker-attributed segments")
        parsed_rows.append(row)

    if len(parsed_rows) != len(samples):
        return fail("samples array must mirror non-empty gt.jsonl rows")

    discoverable = list((model_dir / "fixture").glob("**/gt.jsonl"))
    if not discoverable:
        return fail("validate.py cannot discover any model_dir/fixture/**/gt.jsonl")

    print(f"check_fixture OK: {len(samples)} sample(s), staged_dir={staged_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
