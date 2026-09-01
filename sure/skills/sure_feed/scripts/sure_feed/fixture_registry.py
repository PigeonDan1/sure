from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg"}
SPEECH_UNDERSTANDING_SUBTASK_ORDER = ("asr", "s2tt", "slu", "ser", "gr", "sd", "sa_asr")


def normalize_task(task: str) -> str:
    value = str(task or "").strip().lower().replace("-", "_")
    if value in {"sa_asr", "saasr"}:
        return "sa_asr"
    return value


def find_repo_root(start: Path | None = None) -> Path:
    starts = [start] if start else []
    starts.extend([Path.cwd(), Path(__file__).resolve()])
    for item in starts:
        if item is None:
            continue
        base = item if item.is_dir() else item.parent
        for path in (base, *base.parents):
            if (path / "fixtures" / "tasks").is_dir():
                return path
    return Path.cwd()


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
        if len(rows) >= limit:
            break
    return rows


def _read_json(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)][:limit]
    if isinstance(value, dict):
        rows = value.get("samples") or value.get("items") or []
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)][:limit]
    return []


def _path_from_row(row: dict[str, Any], sample_dir: Path, repo_root: Path) -> str | None:
    value = row.get("audio") or row.get("wav") or row.get("audio_path") or row.get("source_audio")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = sample_dir / path
    return _rel(path, repo_root)


def _compact_sample(row: dict[str, Any], sample_dir: Path, repo_root: Path) -> dict[str, Any]:
    sample: dict[str, Any] = {}
    for key in (
        "id",
        "key",
        "task",
        "language",
        "target_language",
        "dataset",
        "ground_truth",
        "text",
        "label",
        "expected",
        "prompt",
        "keywords",
        "num_speakers",
        "speakers",
        "annotation_format",
        "metric_format",
        "segments",
        "duration",
        "speech_segments",
        "frame_scores",
        "rttm",
        "stm",
        "uem",
        "description",
    ):
        if key in row:
            sample[key] = row[key]
    audio = _path_from_row(row, sample_dir, repo_root)
    if audio:
        sample["audio"] = audio
    return sample


def _candidate_text(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return ""
    fields: list[str] = []
    for key in ("model_id", "repo", "description", "model_card_text", "readme", "pipeline_tag"):
        value = candidate.get(key)
        if value:
            fields.append(str(value))
    fields.extend(str(item) for item in candidate.get("tasks") or [])
    fields.extend(str(item) for item in candidate.get("tags") or [])
    return "\n".join(fields).lower()


def _prefer_gt_files(task: str, gt_files: list[Path], candidate_text: str) -> list[Path]:
    if task != "asr":
        return gt_files
    zh_hint = any(term in candidate_text for term in ("chinese", "mandarin", "cantonese", "zh", "中文", "普通话"))
    en_hint = any(term in candidate_text for term in ("english", "en", "librispeech"))

    def score(path: Path) -> tuple[int, str]:
        text = path.as_posix().lower()
        if zh_hint and ("asr_zh" in text or "/zh" in text):
            return (0, text)
        if en_hint and ("asr_en" in text or "/en" in text):
            return (0, text)
        if "asr_en" in text:
            return (1, text)
        if "asr_zh" in text:
            return (2, text)
        return (3, text)

    return sorted(gt_files, key=score)


def io_contract_for_task(task: str) -> dict[str, Any]:
    normalized = normalize_task(task)
    if normalized in {"tts", "vc"}:
        return {
            "input_type": "text_with_reference_audio" if normalized == "tts" else "audio_pair",
            "output_type": "json",
            "primary_field": "audio_path",
            "required_fields": ["audio_path"],
            "nonempty_fields": ["audio_path"],
            "json_serializable": True,
        }
    if normalized == "s2tt":
        primary = "translation"
    elif normalized in {"ser", "gr", "kws"}:
        primary = "label"
    elif normalized == "slu":
        primary = "answer"
    elif normalized == "vad":
        primary = "speech_segments"
    elif normalized in {"sd", "sa_asr"}:
        primary = "segments"
    else:
        primary = "text"
    return {
        "input_type": "audio_path",
        "output_type": "json",
        "primary_field": primary,
        "required_fields": [primary],
        "nonempty_fields": [primary],
        "json_serializable": True,
    }


def _apply_task_specific_fields(task: str, fixture: dict[str, Any], samples: list[dict[str, Any]]) -> None:
    if not samples:
        return
    first = samples[0]
    first_audio = first.get("audio")
    if isinstance(first_audio, str) and first_audio:
        fixture["audio"] = first_audio
    if task == "tts":
        if isinstance(first.get("text"), str):
            fixture["text"] = first["text"]
        if isinstance(first_audio, str):
            fixture["reference_audio"] = first_audio
    elif task == "vc":
        audios = [sample.get("audio") for sample in samples if isinstance(sample.get("audio"), str)]
        if audios:
            fixture["audio"] = audios[0]
            fixture["source_audio"] = audios[0]
        if len(audios) > 1:
            fixture["reference_audio"] = audios[1]
        elif audios:
            fixture["reference_audio"] = audios[0]
    elif task == "kws":
        positives = [sample for sample in samples if str(sample.get("label") or sample.get("expected") or "").lower() in {"positive", "detect"}]
        negatives = [sample for sample in samples if str(sample.get("label") or sample.get("expected") or "").lower() in {"negative", "reject"}]
        if positives and positives[0].get("audio"):
            fixture["audio"] = positives[0]["audio"]
            fixture["positive_audio"] = positives[0]["audio"]
        if negatives and negatives[0].get("audio"):
            fixture["negative_audio"] = negatives[0]["audio"]
        if first.get("keywords"):
            fixture["keywords"] = first["keywords"]
    elif task == "slu" and first.get("prompt"):
        fixture["prompt"] = first["prompt"]
    if first.get("language"):
        fixture["language"] = first["language"]
    if first.get("target_language"):
        fixture["target_language"] = first["target_language"]


def select_atomic_fixture(
    task: str,
    candidate: dict[str, Any] | None = None,
    repo_root: Path | None = None,
    max_samples: int = 3,
) -> tuple[dict[str, Any] | None, dict[str, Any], list[str]]:
    normalized = normalize_task(task)
    root = repo_root or find_repo_root()
    task_dir = root / "fixtures" / "tasks" / normalized
    index_path = task_dir / "README.md"
    issues: list[str] = []
    if not index_path.exists():
        return None, io_contract_for_task(normalized), [f"missing:fixture.index.{normalized}"]

    text = _candidate_text(candidate)
    gt_files = _prefer_gt_files(normalized, sorted(task_dir.glob("**/gt.jsonl")), text)
    manifest_files = sorted(task_dir.glob("**/manifest.json"))
    samples: list[dict[str, Any]] = []
    fixture_root: Path | None = None
    gt_path: Path | None = None
    manifest_path: Path | None = None
    if gt_files:
        gt_path = gt_files[0]
        fixture_root = gt_path.parent
        rows = _read_jsonl(gt_path, max_samples)
        samples = [_compact_sample(row, fixture_root, root) for row in rows]
    elif manifest_files:
        manifest_path = manifest_files[0]
        fixture_root = manifest_path.parent
        rows = _read_json(manifest_path, max_samples)
        samples = [_compact_sample(row, fixture_root, root) for row in rows]
    else:
        audio_files = sorted(path for path in task_dir.glob("**/*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS)
        if audio_files:
            fixture_root = audio_files[0].parent
            samples = [{"audio": _rel(path, root), "key": path.stem} for path in audio_files[:max_samples]]

    if not fixture_root or not samples:
        issues.append(f"missing:fixture.samples.{normalized}")
        return None, io_contract_for_task(normalized), issues

    fixture: dict[str, Any] = {
        "fixture_id": f"{normalized}/{_rel(fixture_root, root).removeprefix(f'fixtures/tasks/{normalized}/')}",
        "fixture_source": "task_registry",
        "fixture_index": _rel(index_path, root),
        "fixture_root": _rel(fixture_root, root),
        "task_specific": True,
        "fallback_allowed": False,
        "sample_count": len(samples),
        "samples": samples,
    }
    if gt_path:
        fixture["gt"] = _rel(gt_path, root)
    if manifest_path:
        fixture["manifest"] = _rel(manifest_path, root)
        if normalized in {"sd", "sa_asr"}:
            fixture["requires_meeteval_annotation"] = True
    _apply_task_specific_fields(normalized, fixture, samples)
    return fixture, io_contract_for_task(normalized), issues


def infer_speech_understanding_subtasks(candidate: dict[str, Any] | None) -> list[str]:
    text = _candidate_text(candidate)
    subtasks: list[str] = []

    def add(task: str, patterns: tuple[str, ...]) -> None:
        if task not in subtasks and any(re.search(pattern, text) for pattern in patterns):
            subtasks.append(task)

    add("asr", (r"\basr\b", r"automatic speech recognition", r"speech[- ]to[- ]text", r"transcrib", r"transcription"))
    add("s2tt", (r"\bs2tt\b", r"speech translation", r"speech[- ]to[- ]text translation", r"translate"))
    add("slu", (r"\bslu\b", r"spoken language understanding", r"speech understanding", r"reasoning", r"question answering"))
    add("ser", (r"\bser\b", r"speech emotion", r"emotion recognition"))
    add("gr", (r"gender recognition", r"speaker gender"))
    add("sd", (r"speaker diarization", r"\bdiari[sz]ation\b", r"\bdiari[sz]e\b"))
    add("sa_asr", (r"speaker[- ]attributed", r"speaker[- ]aware asr", r"\bsa[-_ ]asr\b"))
    return [task for task in SPEECH_UNDERSTANDING_SUBTASK_ORDER if task in subtasks]


def select_fixture_for_task(
    task: str,
    candidate: dict[str, Any] | None = None,
    repo_root: Path | None = None,
    max_samples: int = 3,
) -> tuple[dict[str, Any] | None, dict[str, Any], list[str], list[dict[str, Any]]]:
    normalized = normalize_task(task)
    root = repo_root or find_repo_root()
    evidence: list[dict[str, Any]] = []
    if normalized != "speech_understanding":
        fixture, io_contract, issues = select_atomic_fixture(normalized, candidate, root, max_samples)
        if fixture:
            evidence.extend(_fixture_evidence(fixture, io_contract))
        return fixture, io_contract, issues, evidence

    composite_index = root / "fixtures" / "tasks" / "speech_understanding" / "README.md"
    if not composite_index.exists():
        return None, io_contract_for_task("asr"), ["missing:fixture.index.speech_understanding"], evidence
    subtasks = infer_speech_understanding_subtasks(candidate)
    if not subtasks:
        return (
            None,
            io_contract_for_task("asr"),
            ["missing:fixture.speech_understanding_subtask"],
            [
                {
                    "source": "local",
                    "field": "fixture_registry.composite_index",
                    "value": _rel(composite_index, root),
                    "strength": "medium",
                    "model_input_field": "fixture",
                }
            ],
        )

    selected: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    issues: list[str] = []
    for subtask in subtasks:
        sub_fixture, sub_contract, sub_issues = select_atomic_fixture(subtask, candidate, root, max_samples)
        if sub_fixture:
            selected.append((subtask, sub_fixture, sub_contract))
        issues.extend(sub_issues)
    if not selected:
        return None, io_contract_for_task("asr"), issues or ["missing:fixture.samples.speech_understanding"], evidence

    primary_task, primary_fixture, primary_contract = selected[0]
    fixture = dict(primary_fixture)
    fixture["fixture_id"] = f"speech_understanding/{primary_fixture['fixture_id']}"
    fixture["fixture_index"] = _rel(composite_index, root)
    fixture["atomic_fixture_index"] = primary_fixture.get("fixture_index")
    fixture["selected_subtasks"] = subtasks
    fixture["primary_subtask"] = primary_task
    fixture["subtask_fixtures"] = [
        {
            "task_type": subtask,
            "fixture_index": sub_fixture.get("fixture_index"),
            "fixture_root": sub_fixture.get("fixture_root"),
            "gt": sub_fixture.get("gt"),
            "manifest": sub_fixture.get("manifest"),
            "sample_count": sub_fixture.get("sample_count"),
        }
        for subtask, sub_fixture, _sub_contract in selected
    ]
    evidence.append(
        {
            "source": "local",
            "field": "fixture_registry.composite_index",
            "value": _rel(composite_index, root),
            "strength": "strong",
            "model_input_field": "fixture",
        }
    )
    evidence.extend(_fixture_evidence(fixture, primary_contract))
    return fixture, primary_contract, issues, evidence


def _fixture_evidence(fixture: dict[str, Any], io_contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source": "local",
            "field": "fixture_registry.index",
            "value": fixture.get("fixture_index"),
            "strength": "strong",
            "model_input_field": "fixture",
        },
        {
            "source": "local",
            "field": "fixture_registry.samples",
            "value": {
                "fixture_root": fixture.get("fixture_root"),
                "sample_count": fixture.get("sample_count"),
                "first_audio": fixture.get("audio"),
            },
            "strength": "strong",
            "model_input_field": "fixture",
        },
        {
            "source": "local",
            "field": "fixture_registry.io_contract",
            "value": io_contract,
            "strength": "strong",
            "model_input_field": "io_contract",
        },
    ]
