#!/usr/bin/env python3
"""Validate the harness mirror of the read-only main-flow agent contracts.

The source of truth is the original SURE-EVAL main-flow agent directory. This
script may read from it and mirror files into this skill package, but it never
writes to the source tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_ROOT = Path(os.environ.get("SURE_MAIN_FLOW_SOURCE_ROOT", "<legacy-sure-eval-root>/docs/agents/main_flow_agent"))
SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MIRROR_ROOT = SKILL_ROOT / "references" / "main_flow_agent"
DEFAULT_MANIFEST = DEFAULT_MIRROR_ROOT / "main_flow_reference_manifest.json"

MIRRORED_SUBDIRS = ("contracts", "templates")
REQUIRED_FILES = (
    "contracts/prediction_output_contract.md",
    "contracts/tts_vc_audio_evaluation_surface.md",
    "templates/run_single_model.sh",
    "templates/run_single_model_single_dataset.sh",
    "templates/run_audio_evaluation_only.sh",
    "templates/protocol.yaml",
    "templates/report_snapshot.md",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_source_files(source_root: Path) -> list[Path]:
    files: list[Path] = []
    for subdir in MIRRORED_SUBDIRS:
        root = source_root / subdir
        if not root.is_dir():
            raise FileNotFoundError(f"main-flow source subdir not found: {root}")
        files.extend(path for path in root.rglob("*") if path.is_file())
    return sorted(files)


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _build_manifest(source_root: Path, mirror_root: Path, files: list[Path]) -> dict[str, Any]:
    entries: list[dict[str, str]] = []
    for source in files:
        relative = source.relative_to(source_root)
        mirror = mirror_root / relative
        entries.append(
            {
                "source_root": str(source_root),
                "source_path": str(relative),
                "mirror_path": str(mirror.relative_to(SKILL_ROOT)),
                "sha256": _sha256(source),
            }
        )
    return {
        "schema": "sure.main_flow_reference_manifest.v1",
        "source_root": str(source_root),
        "mirror_root": str(mirror_root.relative_to(SKILL_ROOT)),
        "mirrored_subdirs": list(MIRRORED_SUBDIRS),
        "required_files": list(REQUIRED_FILES),
        "entries": entries,
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"manifest root must be a JSON object: {path}")
    return payload


def _entry_map(manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ValueError("manifest.entries must be a list")
    mapped: dict[str, dict[str, str]] = {}
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"manifest.entries[{index}] must be an object")
        source_path = raw_entry.get("source_path")
        mirror_path = raw_entry.get("mirror_path")
        sha = raw_entry.get("sha256")
        if not isinstance(source_path, str) or not isinstance(mirror_path, str) or not isinstance(sha, str):
            raise ValueError(f"manifest.entries[{index}] is missing source_path, mirror_path, or sha256")
        mapped[source_path] = {"mirror_path": mirror_path, "sha256": sha}
    return mapped


def _validate(source_root: Path, manifest_path: Path) -> list[str]:
    errors: list[str] = []
    manifest = _read_manifest(manifest_path)
    entries = _entry_map(manifest)

    for required in REQUIRED_FILES:
        if required not in entries:
            errors.append(f"required file missing from manifest: {required}")

    current_files = {str(path.relative_to(source_root)): path for path in _iter_source_files(source_root)}
    missing_from_manifest = sorted(set(current_files) - set(entries))
    stale_manifest_entries = sorted(set(entries) - set(current_files))
    for relative in missing_from_manifest:
        errors.append(f"source file missing from manifest: {relative}")
    for relative in stale_manifest_entries:
        errors.append(f"manifest entry has no source file: {relative}")

    for relative, source in sorted(current_files.items()):
        entry = entries.get(relative)
        if entry is None:
            continue
        expected_sha = entry["sha256"]
        actual_source_sha = _sha256(source)
        if actual_source_sha != expected_sha:
            errors.append(f"source hash drift for {relative}: manifest={expected_sha} actual={actual_source_sha}")
        mirror = SKILL_ROOT / entry["mirror_path"]
        if not mirror.is_file():
            errors.append(f"mirror file missing for {relative}: {mirror}")
            continue
        actual_mirror_sha = _sha256(mirror)
        if actual_mirror_sha != expected_sha:
            errors.append(f"mirror hash drift for {relative}: manifest={expected_sha} actual={actual_mirror_sha}")

    for required in REQUIRED_FILES:
        source = source_root / required
        if not source.is_file():
            errors.append(f"required source file not found: {source}")
    return errors


def _sync(source_root: Path, mirror_root: Path, manifest_path: Path) -> None:
    files = _iter_source_files(source_root)
    for source in files:
        relative = source.relative_to(source_root)
        _copy_file(source, mirror_root / relative)
    manifest = _build_manifest(source_root, mirror_root, files)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or refresh the read-only main-flow reference mirror")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT), help="Read-only docs/agents/main_flow_agent root")
    parser.add_argument("--mirror-root", default=str(DEFAULT_MIRROR_ROOT), help="Harness-local mirror root")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Harness-local manifest path")
    parser.add_argument("--sync", action="store_true", help="Refresh mirror files and manifest from the source root")
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    mirror_root = Path(args.mirror_root).resolve()
    manifest_path = Path(args.manifest).resolve()
    if not source_root.is_dir():
        print(f"source root not found: {source_root}", file=sys.stderr)
        return 2

    if args.sync:
        _sync(source_root, mirror_root, manifest_path)

    errors = _validate(source_root, manifest_path)
    if errors:
        print("main-flow reference check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"main-flow reference OK: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
