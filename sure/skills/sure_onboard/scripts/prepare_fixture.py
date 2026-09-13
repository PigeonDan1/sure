#!/usr/bin/env python3
"""Stage task fixtures into the model-local fixture directory.

This helper is intentionally narrow: it chooses a fixture source from
spec_validation/model_input evidence, copies it under
sure/models/<model>/fixture/<task>/<fixture_name>/, and writes
fixture_manifest.json for the PREPARE_FIXTURE gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "runtime" / "evaluation" / "task_registry.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.runtime.evaluation.task_registry import (
    normalize_task,
    speech_understanding_tasks,
    task_profile,
)

AUDIO_FIELDS = (
    "audio",
    "wav",
    "audio_path",
    "source_audio",
    "reference_audio",
    "noisy_audio",
    "mixed_audio",
    "enrollment_audio",
    "prompt_audio",
)
ANNOTATION_FIELDS = (
    "ground_truth",
    "target_text",
    "reference_text",
    "text",
    "segments",
    "speech_segments",
    "label",
    "intent",
    "speaker_id",
)

def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def canonical_task(task: str) -> str:
    return normalize_task(task)


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


def candidate_from_model_input(resolved: dict[str, Any], repo_root: Path) -> Path | None:
    normalized = resolved.get("normalized_model_input")
    fixture = normalized.get("fixture") if isinstance(normalized, dict) else None
    if not isinstance(fixture, dict):
        return None
    raw = fixture.get("fixture_path") or fixture.get("fixture_root")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    candidates = [path] if path.is_absolute() else [repo_root / path]
    for candidate in candidates:
        if candidate.is_file() and candidate.name == "gt.jsonl":
            return candidate.parent
        if candidate.is_dir():
            return candidate
    return None


def default_fixture_dir(repo_root: Path, task: str) -> Path | None:
    normalized = canonical_task(task)
    configured = repo_root / str(task_profile(normalized)["fixture_root"])
    if not configured.exists():
        return None
    return configured if (configured / "gt.jsonl").is_file() else None


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
        audio = next((row.get(field) for field in AUDIO_FIELDS if row.get(field)), None)
        if not isinstance(audio, str) or not audio:
            raise ValueError(f"{gt}:{line_no} must contain a non-empty relative audio/wav field")
        audio_path = Path(audio)
        if audio_path.is_absolute() or ".." in audio_path.parts:
            raise ValueError(f"{gt}:{line_no} audio path must be relative and stay inside the fixture directory")
        if not (source_dir / audio_path).exists():
            raise FileNotFoundError(f"Fixture audio referenced by {gt}:{line_no} does not exist: {audio}")
        key = row.get("key") or row.get("id") or audio_path.stem
        annotation_fields = [field for field in ANNOTATION_FIELDS if field in row]
        if not annotation_fields:
            raise ValueError(
                f"{gt}:{line_no} must contain at least one annotation field "
                f"({', '.join(ANNOTATION_FIELDS)})"
            )
        audio_roles: dict[str, str] = {}
        for field in AUDIO_FIELDS:
            value = row.get(field)
            if not isinstance(value, str) or not value:
                continue
            role_path = Path(value)
            if role_path.is_absolute() or ".." in role_path.parts:
                raise ValueError(f"{gt}:{line_no} {field} must stay inside the fixture directory")
            if not (source_dir / role_path).is_file():
                raise FileNotFoundError(f"Fixture {field} referenced by {gt}:{line_no} does not exist: {value}")
            audio_roles[field] = str((source_dir / role_path).resolve())
        sample = {
            "key": str(key),
            "audio": audio,
            "audio_path": str((source_dir / audio_path).resolve()),
            "annotation_fields": annotation_fields,
            "audio_roles": audio_roles,
        }
        if isinstance(row.get("duration_sec"), (int, float)):
            sample["duration_sec"] = row["duration_sec"]
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


def stage_fixture(repo_root: Path, model_dir: Path, task: str) -> dict[str, Any]:
    source_dir = default_fixture_dir(repo_root, task)
    if source_dir is None:
        raise FileNotFoundError(f"No fixture source found for task {task}")
    source_samples = load_samples(source_dir)
    staged_dir = model_dir / "fixture" / task / source_dir.name
    replace_tree(source_dir, staged_dir)
    staged_samples = load_samples(staged_dir)
    return {
        "task_type": task,
        "source_dir": str(source_dir),
        "staged_dir": str(staged_dir),
        "gt_jsonl": str(staged_dir / "gt.jsonl"),
        "sample_count": len(staged_samples),
        "samples": staged_samples,
        "source_sample_count": len(source_samples),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True)
    parser.add_argument("--source-dir")
    parser.add_argument("--link-policy", choices=["copy"], default="copy")
    parser.add_argument("--fixture-source", choices=["task_registry", "model_specific", "web_temporary"])
    parser.add_argument("--fixture-url")
    parser.add_argument("--fixture-license")
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

    if task == "speech_understanding":
        if args.source_dir:
            print(
                "--source-dir cannot represent the complete speech_understanding suite; "
                "use the generated task registry fixtures",
                file=sys.stderr,
            )
            return 1
        try:
            subtask_fixtures = [
                stage_fixture(repo_root, model_dir, subtask)
                for subtask in speech_understanding_tasks()
            ]
        except (FileNotFoundError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        primary = subtask_fixtures[0]
        manifest = {
            "model_id": resolved.get("model_id", ""),
            "model_name": resolved.get("model_name", ""),
            "model_dir": str(model_dir),
            "task_type": task,
            "source_dir": primary["source_dir"],
            "staged_dir": primary["staged_dir"],
            "gt_jsonl": primary["gt_jsonl"],
            "sample_count": primary["sample_count"],
            "link_policy": args.link_policy,
            "samples": primary["samples"],
            "suite_members": list(speech_understanding_tasks()),
            "subtask_fixtures": subtask_fixtures,
            "validation_payload_env": "SURE_VALIDATE_INPUT_JSON",
            "notes": "All engine-bound speech_understanding fixtures were staged; the ASR fixture remains the primary bounded infer payload.",
        }
        write_json(Path(args.produces), manifest)
        print(
            f"Prepared speech_understanding suite: {len(subtask_fixtures)} task fixtures, "
            f"primary={primary['staged_dir']}"
        )
        return 0

    normalized_input = resolved.get("normalized_model_input") if isinstance(resolved.get("normalized_model_input"), dict) else {}
    fixture_config = normalized_input.get("fixture") if isinstance(normalized_input.get("fixture"), dict) else {}
    if args.source_dir:
        source_dir = Path(args.source_dir)
        if not source_dir.is_absolute():
            source_dir = repo_root / source_dir
        source_dir = source_dir.resolve()
    else:
        source_dir = candidate_from_model_input(resolved, repo_root) or candidate_from_spec(run_dir, repo_root, task_raw)
        if source_dir is None and fixture_config.get("fixture_status") == "needs_input":
            print(
                "Fixture resolution is required: provide --source-dir with a custom or temporary fixture",
                file=sys.stderr,
            )
            return 2
        source_dir = source_dir or default_fixture_dir(repo_root, task_raw)
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

    registry_root = (repo_root / "fixtures" / "tasks" / task).resolve()
    try:
        source_dir.relative_to(registry_root)
        is_registry_fixture = True
    except ValueError:
        is_registry_fixture = False
    fixture_source = args.fixture_source or fixture_config.get("fixture_source")
    if fixture_source == "task_registry" and not is_registry_fixture:
        print("task_registry fixtures must come from fixtures/tasks/<task>", file=sys.stderr)
        return 1
    if fixture_source in {None, "unresolved"}:
        fixture_source = "task_registry" if is_registry_fixture else "model_specific"
    provenance = dict(fixture_config.get("provenance") or {}) if isinstance(fixture_config.get("provenance"), dict) else {}
    if args.fixture_url:
        provenance["url"] = args.fixture_url
    if args.fixture_license:
        provenance["license"] = args.fixture_license
    if fixture_source == "web_temporary" and (not provenance.get("url") or not provenance.get("license")):
        print("web_temporary fixtures require --fixture-url and --fixture-license", file=sys.stderr)
        return 1
    provenance["source_sha256"] = tree_sha256(source_dir)
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
        "fixture_source": fixture_source,
        "official": fixture_source == "task_registry",
        "fixture_sha256": tree_sha256(staged_dir),
        "provenance": provenance,
        "validation_payload_env": "SURE_VALIDATE_INPUT_JSON",
        "notes": "Fixture staged into model-local fixture directory for validate.py discovery.",
    }
    write_json(Path(args.produces), manifest)
    print(f"Prepared {len(samples)} fixture sample(s): {staged_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
