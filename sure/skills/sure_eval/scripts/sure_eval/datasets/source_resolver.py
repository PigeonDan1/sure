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
import sys
from dataclasses import dataclass
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy

_configured_policy = load_site_policy()
DEFAULT_SOURCE_ROOT = (
    str(_configured_policy["policy"]["datasets"]["allowed_source_roots"][0])
    if _configured_policy
    else ""
)
SOURCE_ROOT_ENV = "SURE_DATASET_SOURCE_ROOT"


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


def accepted_source_root() -> str:
    override = os.environ.get(SOURCE_ROOT_ENV, "").strip()
    if override:
        return override
    if DEFAULT_SOURCE_ROOT:
        return DEFAULT_SOURCE_ROOT
    resolved = load_site_policy(required=True)
    return str(resolved["policy"]["datasets"]["allowed_source_roots"][0])


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


def resolve_aispeech_source_entry(entry: str, explicit_version: str | None = None) -> DatasetSourceRef:
    raw_root, embedded_version = split_source_entry(entry)
    if embedded_version and explicit_version and embedded_version != explicit_version:
        raise SourceResolutionError(
            f"conflicting versions for {raw_root}: entry says {embedded_version}, "
            f"caller says {explicit_version}"
        )
    explicit_version = explicit_version or embedded_version
    root = Path(accepted_source_root())
    path = Path(raw_root)
    if not _is_under(path, root):
        raise SourceResolutionError(
            f"dataset source root must live under {root}, got: {path}. "
            f"Expected form: {root}/.../ds_pool/<source_dataset_name>"
        )
    if path.parent.name != "ds_pool":
        raise SourceResolutionError(
            f"dataset source root must point at ds_pool/<source_dataset_name>, got: {path}"
        )
    if not path.is_dir():
        raise SourceResolutionError(f"dataset source root does not exist: {path}")

    sample_files = path / "sample_files"
    versions = (
        sorted(item.name for item in sample_files.iterdir() if item.is_dir())
        if sample_files.is_dir()
        else []
    )
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

    sample_jsonl = sample_files / version_id / "sample.jsonl"
    ds_jsonl = sample_files / version_id / "ds.jsonl"
    raw_dir = path / "raws" / "sample"
    if not sample_jsonl.is_file():
        raise SourceResolutionError(f"sample.jsonl not found: {sample_jsonl}")
    if not ds_jsonl.is_file():
        raise SourceResolutionError(f"ds.jsonl not found: {ds_jsonl}")
    if not raw_dir.is_dir():
        raise SourceResolutionError(f"raws/sample not found: {raw_dir}")

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
