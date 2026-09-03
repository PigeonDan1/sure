#!/usr/bin/env python3
"""Resolve dataset source roots into canonical dataset identities.

Main-flow evaluation accepts dataset inputs only as source roots under the
active site policy's configured storage root; ``SURE_DATASET_SOURCE_ROOT``
remains an explicit test and local-run override. The canonical dataset id
derived here is ``<source_dataset_name>__<version_id>`` with no task suffix.
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


def _configured_source_roots() -> dict[str, str]:
    source_roots = DEFAULT_SOURCE_ROOTS
    if not source_roots:
        resolved = load_site_policy(required=True)
        source_roots = resolved["policy"]["datasets"]["allowed_source_roots"]
    return dict(source_roots)


def get_allowed_source_root(key: str) -> str:
    """Look up a dataset source root by key from the configured allowed_source_roots."""
    source_roots = _configured_source_roots()
    if key not in source_roots:
        available = ", ".join(sorted(source_roots.keys())) if source_roots else "none"
        raise SourceResolutionError(
            f"dataset_source_key '{key}' not found in allowed_source_roots. Available keys: {available}"
        )
    return source_roots[key]


def accepted_source_root(key: str | None = None) -> str:
    override = os.environ.get(SOURCE_ROOT_ENV, "").strip()
    if override:
        # A key fits sure.site.loader's key grammar; anything else (a posix path, a Windows path
        # with no "/" in it) is the pre-map raw-path override.
        if _SOURCE_KEY_RE.fullmatch(override):
            return get_allowed_source_root(override)
        return override
    if key:
        return get_allowed_source_root(key)
    # Default to "default" key if not specified
    return get_allowed_source_root("default")


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


def _rejected_root_hint(path: Path) -> str:
    """What the caller should have passed, so a rejection is not a guessing game.

    The site configures several roots under distinct keys. Saying only "must live
    under <default>" left every caller with a path under another configured key to
    find that key by trial: twenty runs died on this one message in two days.
    """
    try:
        source_roots = _configured_source_roots()
    except Exception:  # a rejection message must not fail on top of the rejection
        return ""
    for key, candidate in sorted(source_roots.items()):
        if _is_under(path, Path(candidate)):
            return f"This path is under allowed_source_roots key '{key}'; pass dataset_source_key={key}. "
    listed = ", ".join(f"{key}={value}" for key, value in sorted(source_roots.items()))
    return f"Configured allowed_source_roots: {listed}. " if listed else ""


def resolve_site_source_entry(entry: str, explicit_version: str | None = None, dataset_source_key: str | None = None) -> DatasetSourceRef:
    raw_root, embedded_version = split_source_entry(entry)
    if embedded_version and explicit_version and embedded_version != explicit_version:
        raise SourceResolutionError(
            f"conflicting versions for {raw_root}: entry says {embedded_version}, "
            f"caller says {explicit_version}"
        )
    explicit_version = explicit_version or embedded_version
    root = Path(accepted_source_root(dataset_source_key))
    path = Path(raw_root)
    if not _is_under(path, root):
        raise SourceResolutionError(
            f"dataset source root must live under {root}, got: {path}. "
            f"{_rejected_root_hint(path)}"
            f"Expected form: {root}/.../<source_dataset_name>"
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
