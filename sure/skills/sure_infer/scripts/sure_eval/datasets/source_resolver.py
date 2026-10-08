#!/usr/bin/env python3
"""Resolve dataset source roots into canonical dataset identities.

Main-flow evaluation accepts dataset inputs only as source roots under the
active site policy's configured ``allowed_source_roots``. Acceptance is the
union of those roots: each dataset entry is matched independently and is
accepted when it lives under any of them, so one run may draw datasets from
several configured roots. ``dataset_source_key`` is validated and recorded but
no longer selects the root. ``SURE_DATASET_SOURCE_ROOT`` remains an explicit
test and local-run override and carries a host path only; a value shaped like a
key is rejected, not silently ignored. The canonical dataset
id derived here is ``<source_dataset_name>__<version_id>`` with no task suffix.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy

_configured_policy = load_site_policy()
DEFAULT_SOURCE_ROOTS = (
    _configured_policy["policy"]["datasets"]["allowed_source_roots"]
    if _configured_policy
    else {}
)
SOURCE_ROOT_ENV = "SURE_DATASET_SOURCE_ROOT"
_SOURCE_KEY_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")  # the allowed_source_roots key grammar (sure.site.loader)
_SOURCE_TASK_ALIASES = {
    "asr": "ASR",
    "classification": "CLASSIFICATION",
    "gr": "GR",
    "kws": "KWS",
    "lid": "LID",
    "language_identification": "LID",
    "spoken_language_identification": "LID",
    "s2tt": "S2TT",
    "sa-asr": "SA-ASR",
    "sa_asr": "SA-ASR",
    "sd": "SD",
    "se": "SE",
    "ser": "SER",
    "slu": "SLU",
    "sv": "SV",
    "tse": "TSE",
    "tts": "TTS",
    "vad": "VAD",
    "voice_activity_detection": "VAD",
    "vc": "VC",
}


class SourceResolutionError(ValueError):
    """Raised when a dataset source entry cannot be resolved."""


@dataclass(frozen=True)
class DatasetSourceRef:
    source_root: str
    source_dataset_name: str
    version_id: str
    dataset_id: str
    sample_jsonl: str
    ds_jsonl: str
    raw_dir: str
    supported_tasks: tuple[str, ...] = ()
    # The configured root key this dataset's own path matched (None when no root matched,
    # which only happens on the error path), and every key it matched when roots nest.
    source_root_key: str | None = None
    source_root_keys: tuple[str, ...] = ()


def configured_source_roots() -> dict[str, str]:
    """Every configured allowed_source_roots entry, as key -> path."""
    source_roots = DEFAULT_SOURCE_ROOTS
    if not source_roots:
        resolved = load_site_policy(required=True)
        source_roots = resolved["policy"]["datasets"]["allowed_source_roots"]
    return dict(source_roots)


def get_allowed_source_root(key: str) -> str:
    """Look up a dataset source root by key from the configured allowed_source_roots."""
    source_roots = configured_source_roots()
    if key not in source_roots:
        available = ", ".join(sorted(source_roots.keys())) if source_roots else "none"
        raise SourceResolutionError(
            f"dataset_source_key '{key}' not found in allowed_source_roots. Available keys: {available}"
        )
    return source_roots[key]


def accepted_source_roots() -> dict[str, str]:
    """The roots a dataset path may live under: the configured set, or a path-only override.

    ``SURE_DATASET_SOURCE_ROOT`` carries a host path, never a key. A value that merely fits
    the key grammar is rejected rather than looked up in ``allowed_source_roots`` (which would
    collapse the union back onto one root) or read as a relative path (which would silently
    resolve somewhere else). Raising keeps the boundary loud in the widening direction: before
    the union this value named a root, so a stale script that still sets one must be told,
    not quietly granted every configured root.
    """
    override = os.environ.get(SOURCE_ROOT_ENV, "").strip()
    if not override:
        return configured_source_roots()
    if _SOURCE_KEY_RE.fullmatch(override):
        raise SourceResolutionError(
            f"{SOURCE_ROOT_ENV}='{override}' looks like an allowed_source_roots key, but this "
            f"variable carries a filesystem path only and a key no longer selects a root. Set it "
            f"to the path you want, or unset it to accept every configured root."
        )
    return {"": override}


def accepted_source_root() -> str:
    """Legacy single-root view: the first accepted root.

    The compatibility probe reads the whole set through ``accepted_source_roots()``; this
    stays as the single-root entry point for callers that predate the union.
    """
    roots = accepted_source_roots()
    if not roots:
        raise SourceResolutionError("no dataset source roots are configured in allowed_source_roots")
    return next(iter(roots.values()))


def _match_source_root(path: Path, source_roots: dict[str, str]) -> tuple[str | None, tuple[str, ...]]:
    """Attribute ``path`` to a configured root: longest match, key name breaking ties.

    Returns the selected key (None when no root matches) and every matched key. Roots may
    nest, so one path can match several; under a union that is legal and only attribution
    is affected, so the choice is deterministic rather than an ambiguity error.
    """
    matched = [(key, Path(root)) for key, root in source_roots.items() if _is_under(path, Path(root))]
    if not matched:
        return None, ()
    selected = max(matched, key=lambda item: (len(str(item[1])), item[0]))
    return selected[0], tuple(sorted(key for key, _ in matched))


def split_source_entry(entry: str) -> tuple[str, str | None]:
    """Split ``<root>@<version>`` into (root, version); no valid suffix -> (entry, None)."""
    value = str(entry or "").strip()
    if "@" not in value:
        return value, None
    root, _, version = value.rpartition("@")
    if not root or not version or "/" in version:
        return value, None
    return root, version


def is_source_entry(entry: str) -> bool:
    """A dataset input is treated as a source entry when it is an absolute path."""
    value, _ = split_source_entry(entry)
    value = str(value or "").strip()
    if not value:
        return False
    return value.startswith("/") or Path(value).is_absolute()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _rejected_root_hint() -> str:
    """Name the permitted roots, so a rejection is not a guessing game."""
    try:
        source_roots = accepted_source_roots()  # reflects a path override when one is set
    except Exception:  # a rejection message must not fail on top of the rejection
        return ""
    listed = ", ".join(
        value if not key else f"{key}={value}" for key, value in sorted(source_roots.items())
    )
    return f"Configured allowed_source_roots: {listed}. " if listed else ""


def relative_source_candidates(name: str) -> list[tuple[str, str]]:
    """Accepted roots under which a relative dataset name exists: [(key, absolute_path)].

    Source entries are absolute paths only, so a relative input can never resolve.
    Callers use this to turn that rejection into the exact absolute path the caller
    should have passed. A spelling that could escape a root (an absolute path, or a
    ``..`` segment) is never a candidate, and a candidate must stay inside its root
    even after symlink resolution.
    """
    value, _ = split_source_entry(name)
    value = str(value or "").strip()
    if not value:
        return []
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return []
    try:
        source_roots = accepted_source_roots()
    except Exception:  # a hint must not fail on top of the failure
        return []
    found: list[tuple[str, str]] = []
    for key, root in sorted(source_roots.items()):
        root_path = Path(root)
        candidate = root_path / relative
        try:
            if not candidate.is_dir():
                continue
            resolved = candidate.resolve()
            if not _is_under(resolved, root_path.resolve()):
                continue
        except (OSError, RuntimeError):
            # Unreadable directories and symlink loops must not hide the input error.
            continue
        found.append((key, str(resolved)))
    return found


def resolve_site_source_entry(entry: str, explicit_version: str | None = None, dataset_source_key: str | None = None) -> DatasetSourceRef:
    raw_root, embedded_version = split_source_entry(entry)
    if embedded_version and explicit_version and embedded_version != explicit_version:
        raise SourceResolutionError(
            f"conflicting versions for {raw_root}: entry says {embedded_version}, "
            f"caller says {explicit_version}"
        )
    explicit_version = explicit_version or embedded_version
    if dataset_source_key:
        # Validated for typo protection only: a key never selects or narrows the boundary.
        get_allowed_source_root(dataset_source_key)
    path = Path(raw_root)
    source_root_key, source_root_keys = _match_source_root(path, accepted_source_roots())
    if source_root_key is None:
        raise SourceResolutionError(
            f"dataset source root must live under one of the configured allowed_source_roots, "
            f"got: {path}. {_rejected_root_hint()}"
            f"Expected form: <allowed_source_root>/.../<source_dataset_name>"
        )
    if not path.is_dir():
        raise SourceResolutionError(f"dataset source root does not exist: {path}")

    # Two layouts: the versioned pool layout (sample_files/<version>/sample.jsonl,
    # raws/sample/) and a flat directory that carries sample.jsonl and the audio
    # itself. ds.jsonl and raws/ are optional in both.
    sample_files = path / "sample_files"
    if sample_files.is_dir():
        versions = sorted(item.name for item in sample_files.iterdir() if item.is_dir())
        if not versions:
            raise SourceResolutionError(f"no versions found under {sample_files}")
        if explicit_version:
            if explicit_version not in versions:
                raise SourceResolutionError(
                    f"version {explicit_version} not found under {sample_files}; "
                    f"available: {', '.join(versions)}"
                )
            version_id = explicit_version
        elif len(versions) == 1:
            version_id = versions[0]
        else:
            raise SourceResolutionError(
                f"multiple versions under {sample_files} and no explicit version given: "
                f"{', '.join(versions)}"
            )
        version_dir = sample_files / version_id
    elif (path / "sample.jsonl").is_file():
        version_id = explicit_version or "unversioned"
        version_dir = path
    else:
        raise SourceResolutionError(
            f"no dataset layout under {path}: expected sample_files/<version>/sample.jsonl "
            "or sample.jsonl in the directory itself"
        )

    sample_jsonl = version_dir / "sample.jsonl"
    ds_jsonl = version_dir / "ds.jsonl"
    raw_dir = next(
        (candidate for candidate in (path / "raws" / "sample", path / "raws") if candidate.is_dir()),
        path,
    )
    if not sample_jsonl.is_file():
        raise SourceResolutionError(f"sample.jsonl not found: {sample_jsonl}")

    source_dataset_name = path.name
    return DatasetSourceRef(
        source_root=str(path),
        source_dataset_name=source_dataset_name,
        version_id=version_id,
        dataset_id=f"{source_dataset_name}__{version_id}",
        sample_jsonl=str(sample_jsonl),
        ds_jsonl=str(ds_jsonl),
        raw_dir=str(raw_dir),
        supported_tasks=read_source_supported_tasks(str(ds_jsonl)),
        source_root_key=source_root_key,
        source_root_keys=source_root_keys,
    )


def read_source_language(ref: DatasetSourceRef) -> str:
    """Best-effort language from the version's ds.jsonl (audio.speech.language)."""
    try:
        text = Path(ref.ds_jsonl).read_text(encoding="utf-8").strip()
        payload = json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    speech = (payload.get("audio") or {}).get("speech") or {}
    return str(speech.get("language") or "")


def read_source_metadata(ref: DatasetSourceRef) -> dict[str, str]:
    """Best-effort task/language metadata from the version's ds.jsonl.

    ``audio.speech.language`` is the speech (source) language. A source declares
    a speech-translation dataset either explicitly (top-level ``task``, e.g.
    ``"S2TT"``) or implicitly via ``audio.speech.translation_language``; with no
    declaration the task stays ``ASR`` so existing sources keep their behaviour.
    """
    try:
        text = Path(ref.ds_jsonl).read_text(encoding="utf-8").strip()
        payload = json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError):
        return {"task": "ASR", "language": "", "translation_language": ""}
    if not isinstance(payload, dict):
        return {"task": "ASR", "language": "", "translation_language": ""}
    speech = (payload.get("audio") or {}).get("speech") or {}
    language = str(speech.get("language") or "")
    translation_language = str(speech.get("translation_language") or "")
    declared = str(payload.get("task") or "").strip().upper()
    task = declared or ("S2TT" if translation_language else "ASR")
    return {"task": task, "language": language, "translation_language": translation_language}


def _normalize_source_task(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _SOURCE_TASK_ALIASES.get(normalized, "")


def _normalize_task(value: object) -> str:
    return str(value or "").strip().upper().replace("-", "_")


# Tags that say nothing about what the rows contain. A pool declaring only these
# is as good as undeclared for task resolution.
_GENERIC_SOURCE_TASK_TAGS = {"", "NA", "N/A", "NONE", "OTHER", "UNKNOWN", "UNSPECIFIED"}


def _declared_source_tasks(supported: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(task for task in supported if task not in _GENERIC_SOURCE_TASK_TAGS)


def _read_first_sample(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    payload = json.loads(line)
                    return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _sample_annotation_task(sample: dict[str, object]) -> str:
    sample_task = _normalize_source_task(sample.get("task"))
    if sample_task:
        return sample_task

    annotations = sample.get("annotation")
    if not isinstance(annotations, list):
        return ""
    for annotation in annotations:
        if not isinstance(annotation, dict):
            continue
        timestamp = annotation.get("timestamp")
        if isinstance(timestamp, dict) and (
            timestamp.get("begin_time") is not None or timestamp.get("end_time") is not None
        ):
            return "VAD"
        transcription = annotation.get("transcription")
        if isinstance(transcription, dict) and transcription.get("text") not in (None, "", []):
            return "ASR"
    return ""


def _explicit_task(payload: dict[str, object]) -> str:
    speech = payload.get("audio") if isinstance(payload.get("audio"), dict) else {}
    speech = speech.get("speech") if isinstance(speech, dict) and isinstance(speech.get("speech"), dict) else speech
    for value in (
        payload.get("task"),
        payload.get("task_type"),
        speech.get("task") if isinstance(speech, dict) else None,
        speech.get("task_type") if isinstance(speech, dict) else None,
    ):
        task = _normalize_source_task(value)
        if task:
            return task
    return ""


def read_source_task(ref: DatasetSourceRef) -> str:
    """Resolve a source-root task without guessing from its directory name."""
    sample = _read_first_sample(Path(ref.sample_jsonl))
    sample_task = _explicit_task(sample) or _sample_annotation_task(sample)
    if sample_task:
        return sample_task

    metadata: dict[str, object] = {}
    try:
        text = Path(ref.ds_jsonl).read_text(encoding="utf-8").strip()
        payload = json.loads(text) if text else {}
        if isinstance(payload, dict):
            metadata = payload
    except (OSError, json.JSONDecodeError):
        pass

    metadata_task = _explicit_task(metadata)
    if metadata_task:
        return metadata_task

    supported_tasks = metadata.get("supported_tasks")
    if isinstance(supported_tasks, str):
        supported_tasks = [supported_tasks]
    if isinstance(supported_tasks, list):
        for task in (_normalize_source_task(item) for item in supported_tasks):
            if task:
                return task
    return ""


def read_source_supported_tasks(ds_jsonl: str) -> tuple[str, ...]:
    """Read supported_tasks from a version's ds.jsonl (top-level; tolerant).

    A missing or unreadable field means a legacy ASR dataset, so the callers
    fall back to ASR exactly like the pre-metadata pipeline did. fleurs stores
    the tag at the top level; ``audio.speech.supported_tasks`` is tolerated too.
    """
    try:
        text = Path(ds_jsonl).read_text(encoding="utf-8").strip()
        payload = json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("supported_tasks")
    if raw is None:
        audio = payload.get("audio")
        if not isinstance(audio, dict):
            return ()
        speech = audio.get("speech")
        if not isinstance(speech, dict):
            return ()
        raw = speech.get("supported_tasks")
    if not isinstance(raw, (list, tuple, set)):
        return ()
    tasks: list[str] = []
    for value in raw:
        task = _normalize_task(value)
        if task and task not in tasks:
            tasks.append(task)
    return tuple(tasks)


def source_default_task(ref: DatasetSourceRef, intent: str = "") -> str:
    """Pick the task a source root should be projected as.

    ``intent`` is the run's synthetic-task intent (model task or a ``tts_*`` /
    ``vc_*`` metric hint); empty when the caller has none. Resolution order:
      0. nothing usable declared         -> sample/metadata shape (VAD/LID/S2TT…);
                                            ASR-shaped samples fall through
      1. no declared supported_tasks      -> legacy ASR (explicit non-ASR intent
                                            raises)
      2. intent declared                  -> must be ∈ supported_tasks, else
                                            raise (fail closed, no silent fall-through)
      3. exactly one supported task       -> that task
      4. ASR among several                -> ASR
      5. otherwise                        -> SourceResolutionError
    """
    supported = _declared_source_tasks(ref.supported_tasks)
    intent_task = _normalize_task(intent)
    if not supported:
        detected = read_source_task(ref)
        # S2TT may only show up via read_source_metadata (translation_language).
        if not detected or detected == "ASR":
            try:
                meta_task = read_source_metadata(ref).get("task") or ""
            except Exception:
                meta_task = ""
            if meta_task and meta_task not in {"", "ASR"}:
                detected = meta_task
        if detected and detected != "ASR":
            if not intent_task or intent_task == detected:
                return detected
            raise SourceResolutionError(
                f"dataset {ref.dataset_id} has {detected}-shaped samples but was "
                f"asked to project as {intent_task}"
            )
        if not intent_task or intent_task == "ASR":
            return "ASR"
        raise SourceResolutionError(
            f"dataset {ref.dataset_id} declares no supported_tasks; cannot "
            f"project as {intent_task} (only the legacy ASR default)"
        )
    # When the caller passed a non-empty intent it must be in supported_tasks;
    # otherwise fail closed instead of silently picking ASR / a different task.
    if intent_task and intent_task not in supported:
        raise SourceResolutionError(
            f"dataset {ref.dataset_id} declares supported_tasks "
            f"{', '.join(supported)} but was asked to project as {intent_task}; "
            "the requested task is not in the source's supported_tasks"
        )
    if intent_task:
        # intent_task ∈ supported is enforced above, so a non-empty intent always
        # resolves to itself; ASR / single-task / multi-task fall through only
        # when the caller didn't specify one.
        return intent_task
    if len(supported) == 1:
        return supported[0]
    if "ASR" in supported:
        return "ASR"
    raise SourceResolutionError(
        f"dataset {ref.dataset_id} declares supported_tasks {', '.join(supported)} "
        f"with no ASR fallback; pass the projection task explicitly "
        f"(got intent={intent_task or '(none)'})"
    )
