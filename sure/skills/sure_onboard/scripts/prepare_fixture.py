#!/usr/bin/env python3
"""Stage task fixtures into the model-local fixture directory.

This helper is intentionally narrow: it chooses a fixture source from
spec_validation/model_input evidence, copies it under
sure/models/<model>/fixture/<task>/<fixture_name>/, and writes
fixture_manifest.json for the PREPARE_FIXTURE gate.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def canonical_task(task: str) -> str:
    return task.replace("-", "_").lower()


def infer_repo_root(model_dir: Path) -> Path:
    parts = model_dir.resolve().parts
    for idx in range(len(parts) - 2):
        if parts[idx] == "sure" and parts[idx + 1] == "models":
            return Path(*parts[:idx])
    return Path(__file__).resolve().parents[4]


def candidate_from_spec(run_dir: Path, repo_root: Path, task: str) -> Path | None:
    spec_path = run_dir / "artifacts" / "spec_validation.json"
    if not spec_path.exists():
        return None
    try:
        data = load_json(spec_path)
    except Exception:
        return None
    fixture = (((data.get("checks") or {}).get("fixture_availability") or {}).get("fixture_path"))
    if not isinstance(fixture, str) or not fixture.strip():
        return None
    raw = Path(fixture)
    candidates = [raw] if raw.is_absolute() else [repo_root / raw, repo_root / "fixtures" / "tasks" / canonical_task(task) / raw]
    for candidate in candidates:
        if candidate.is_file() and candidate.name == "gt.jsonl":
            return candidate.parent
        if candidate.is_dir():
            return candidate
    return None


def default_fixture_dir(repo_root: Path, task: str) -> Path | None:
    task_dir = repo_root / "fixtures" / "tasks" / canonical_task(task)
    if not task_dir.exists():
        return None
    if (task_dir / "gt.jsonl").exists():
        return task_dir
    options = sorted(path.parent for path in task_dir.glob("*/gt.jsonl"))
    return options[0] if options else None


def load_samples(source_dir: Path) -> list[dict[str, Any]]:
    gt = source_dir / "gt.jsonl"
    samples: list[dict[str, Any]] = []
    for line_no, line in enumerate(gt.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{gt}:{line_no} is not valid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{gt}:{line_no} must be a JSON object")
        audio = row.get("audio") or row.get("wav") or row.get("prompt_audio") or row.get("reference_audio")
        if not isinstance(audio, str) or not audio:
            raise ValueError(f"{gt}:{line_no} must contain a non-empty relative audio/wav field")
        audio_path = Path(audio)
        if audio_path.is_absolute() or ".." in audio_path.parts:
            raise ValueError(f"{gt}:{line_no} audio path must be relative and stay inside the fixture directory")
        if not (source_dir / audio_path).exists():
            raise FileNotFoundError(f"Fixture audio referenced by {gt}:{line_no} does not exist: {audio}")
        key = row.get("key") or row.get("id") or audio_path.stem
        annotation_fields = [
            field
            for field in (
                "ground_truth",
                "target_text",
                "text",
                "segments",
                "speech_segments",
                "label",
                "intent",
            )
            if field in row
        ]
        if not annotation_fields:
            raise ValueError(
                f"{gt}:{line_no} must contain at least one annotation field "
                "(ground_truth, target_text, text, segments, speech_segments, label, or intent)"
            )
        sample = {
            "key": str(key),
            "audio": audio,
            "audio_path": str((source_dir / audio_path).resolve()),
            "annotation_fields": annotation_fields,
        }
        if isinstance(row.get("duration_sec"), (int, float)):
            sample["duration_sec"] = row["duration_sec"]
        if isinstance(row.get("duration"), (int, float)):
            sample["duration"] = row["duration"]
        if isinstance(row.get("sample_rate"), (int, float)):
            sample["sample_rate"] = row["sample_rate"]
        samples.append(sample)
    if not samples:
        raise ValueError(f"No samples found in {gt}")
    if len(samples) > 5:
        raise ValueError(f"{gt} has {len(samples)} samples; local validation allows at most 5")
    return samples


def replace_tree(source_dir: Path, staged_dir: Path) -> None:
    if staged_dir.exists() or staged_dir.is_symlink():
        if staged_dir.is_symlink() or staged_dir.is_file():
            staged_dir.unlink()
        else:
            shutil.rmtree(staged_dir)
    staged_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, staged_dir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    parser.add_argument("--source-dir")
    parser.add_argument("--link-policy", choices=["copy"], default="copy")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    resolved_path = run_dir / "artifacts" / "model_input_resolved.json"
    if not resolved_path.exists():
        print(f"model_input_resolved.json not found: {resolved_path}", file=sys.stderr)
        return 1
    resolved = load_json(resolved_path)
    model_dir_raw = resolved.get("model_dir")
    task_raw = resolved.get("task_type")
    if not isinstance(model_dir_raw, str) or not isinstance(task_raw, str):
        print("model_input_resolved.json must contain model_dir and task_type", file=sys.stderr)
        return 1
    model_dir = Path(model_dir_raw).resolve()
    task = canonical_task(task_raw)
    repo_root = infer_repo_root(model_dir)

    if args.source_dir:
        source_dir = Path(args.source_dir)
        if not source_dir.is_absolute():
            source_dir = repo_root / source_dir
        source_dir = source_dir.resolve()
    else:
        source_dir = candidate_from_spec(run_dir, repo_root, task_raw) or default_fixture_dir(repo_root, task_raw)
    if source_dir is None or not source_dir.exists():
        print(f"No fixture source found for task {task}. Expected fixtures/tasks/{task}/<fixture>/gt.jsonl", file=sys.stderr)
        return 1
    if source_dir.is_file() and source_dir.name == "gt.jsonl":
        source_dir = source_dir.parent
    if not (source_dir / "gt.jsonl").exists():
        print(f"Fixture source must contain gt.jsonl: {source_dir}", file=sys.stderr)
        return 1

    samples = load_samples(source_dir)
    staged_dir = model_dir / "fixture" / task / source_dir.name
    replace_tree(source_dir, staged_dir)

    staged_samples = []
    for sample in load_samples(staged_dir):
        staged_samples.append(sample)

    manifest = {
        "model_id": resolved.get("model_id", ""),
        "model_name": resolved.get("model_name", ""),
        "model_dir": str(model_dir),
        "task_type": task,
        "source_dir": str(source_dir),
        "staged_dir": str(staged_dir),
        "gt_jsonl": str(staged_dir / "gt.jsonl"),
        "sample_count": len(staged_samples),
        "link_policy": args.link_policy,
        "samples": staged_samples,
        "validation_payload_env": "SURE_VALIDATE_INPUT_JSON",
        "notes": "Fixture staged into model-local fixture directory for validate.py discovery.",
    }
    write_json(Path(args.produces), manifest)
    print(f"Prepared {len(samples)} fixture sample(s): {staged_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
