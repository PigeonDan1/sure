#!/usr/bin/env python3
"""Validate an OpenBench dataset directory without third-party dependencies.

Vendored unchanged from the `openbench-dataset` skill (scripts/validate_dataset.py)
so this package can check an OpenBench-shaped directory without reaching outside
the repository. Stdlib only — no PyYAML. Do not fork it: fixes belong upstream.

Background tooling: nothing in the default conversion path calls it. It is only
useful when exporting a source dataset into OpenBench format or explaining why
the platform rejected one. See references/dataset_formats.md for the format this
enforces and for the ds_pool -> OpenBench field mapping.

Registered in UNIT_AGNOSTIC_SCRIPTS (hooks/index.ts): it writes no artifact and
carries no state-machine position, so any unit may run it. Keep the filename flat
under scripts/ — the preToolCall guard only matches `scripts/<name>.py`, so a
nested path would escape the whitelist instead of being listed in it.

Usage:
    "$HARNESS_PYTHON_BIN" scripts/validate_openbench_dataset.py <dataset_dir> [--skip-media]

exit 0 = VALID (prints record count); non-zero = INVALID (prints errors to stderr).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_META = {"language", "supported_tasks", "type", "license"}
TASKS = {"ASR", "TTS", "WakeUp", "FalseTrigger", "SP", "DOA", "LID", "other"}
TYPES = {"audio", "text", "image", "video"}
ENUMS = {
    "speech_style": {"add", "conv", "unknown"},
    "distance": {"near", "far"},
    "device": {"phone", "hifi", "ondevice", "array", "other", "unknown"},
    "background": {"quiet", "noisy", "mix"},
    "anno_method": {"manu", "other"},
    "generation": {"record", "real", "synthetic", "augment", "other"},
}
SAMPLE_RATES = {8000, 16000, 22050, 23000, 24000, 32000, 44100, 48000, 96000}
TAGS = {"speech", "noise", "music", "audio_event", "echo", "other"}


def _strip_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value.startswith(("[", "{", '"')):
        try:
            return json.loads(value.replace("'", '"'))
        except json.JSONDecodeError:
            pass
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value.strip("'\"")


def parse_front_matter(path: Path) -> tuple[dict[str, Any], list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []
    if not lines or lines[0].strip() != "---":
        return {}, ["README.md must start with YAML front matter (---)"]
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, ["README.md YAML front matter is not closed with ---"]
    data: dict[str, Any] = {}
    current: str | None = None
    for number, raw in enumerate(lines[1:end], 2):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(.*))?$", line)
        if match:
            current, value = match.group(1), match.group(2) or ""
            data[current] = _strip_scalar(value) if value else []
        elif line.startswith("-") and current:
            if not isinstance(data[current], list):
                data[current] = [data[current]]
            data[current].append(_strip_scalar(line[1:].strip()))
        else:
            errors.append(f"README.md:{number}: unsupported YAML line: {raw}")
    if not any(line.strip() for line in lines[end + 1 :]):
        errors.append("README.md must contain Markdown text after front matter")
    return data, errors


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def validate_metadata(meta: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in REQUIRED_META:
        if key not in meta or meta[key] in (None, "", []):
            errors.append(f"README.md: missing required tag '{key}'")
    for value in _as_list(meta.get("supported_tasks", [])):
        if value not in TASKS:
            errors.append(f"README.md: unsupported supported_tasks value: {value}")
    for value in _as_list(meta.get("type", [])):
        if value not in TYPES:
            errors.append(f"README.md: unsupported type value: {value}")
    for key, allowed in ENUMS.items():
        if key in meta:
            for value in _as_list(meta[key]):
                if value not in allowed:
                    errors.append(f"README.md: unsupported {key} value: {value}")
    if "channels" in meta:
        try:
            channels = int(meta["channels"])
            if not 1 <= channels <= 16:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("README.md: channels must be an integer from 1 to 16")
    if "sample_rate" in meta:
        for value in _as_list(meta["sample_rate"]):
            try:
                valid = int(value) in SAMPLE_RATES
            except (TypeError, ValueError):
                valid = False
            if not valid:
                errors.append(f"README.md: unsupported sample_rate value: {value}")
    if "tag" in meta:
        for value in _as_list(meta["tag"]):
            if value not in TAGS:
                errors.append(f"README.md: unsupported tag value: {value}")
    return errors


def validate_records(path: Path, root: Path, skip_media: bool) -> tuple[list[str], int]:
    errors: list[str] = []
    keys: set[str] = set()
    count = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        return [f"sample.jsonl is not valid UTF-8: {exc}"], 0
    for number, line in enumerate(lines, 1):
        if not line.strip():
            errors.append(f"sample.jsonl:{number}: blank lines are not allowed")
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"sample.jsonl:{number}: invalid JSON: {exc.msg}")
            continue
        count += 1
        if not isinstance(record, dict):
            errors.append(f"sample.jsonl:{number}: record must be a JSON object")
            continue
        for field in ("key", "path", "text"):
            if field not in record or not isinstance(record[field], str):
                errors.append(f"sample.jsonl:{number}: '{field}' is required and must be a string")
        key = record.get("key")
        if isinstance(key, str):
            if key in keys:
                errors.append(f"sample.jsonl:{number}: duplicate key '{key}'")
            keys.add(key)
        media = record.get("path")
        if isinstance(media, str):
            media_path = Path(media)
            if media_path.is_absolute() or ".." in media_path.parts:
                errors.append(f"sample.jsonl:{number}: path must stay inside dataset root")
            elif not skip_media and not (root / media_path).is_file():
                errors.append(f"sample.jsonl:{number}: referenced file does not exist: {media}")
        has_start, has_end = "start_time" in record, "end_time" in record
        if has_start != has_end:
            errors.append(f"sample.jsonl:{number}: start_time and end_time must appear together")
        if has_start and has_end:
            start, end = record["start_time"], record["end_time"]
            if not isinstance(start, (int, float)) or isinstance(start, bool):
                errors.append(f"sample.jsonl:{number}: start_time must be numeric")
            if not isinstance(end, (int, float)) or isinstance(end, bool):
                errors.append(f"sample.jsonl:{number}: end_time must be numeric")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end <= start:
                errors.append(f"sample.jsonl:{number}: end_time must be greater than start_time")
    return errors, count


def validate_dataset(root: Path, skip_media: bool = False) -> tuple[list[str], int]:
    errors: list[str] = []
    if not root.is_dir():
        return [f"dataset directory does not exist: {root}"], 0
    readme, sample = root / "README.md", root / "sample.jsonl"
    if not readme.is_file():
        errors.append("missing README.md")
    else:
        meta, meta_errors = parse_front_matter(readme)
        errors.extend(meta_errors)
        errors.extend(validate_metadata(meta))
    count = 0
    if not sample.is_file():
        errors.append("missing sample.jsonl")
    else:
        record_errors, count = validate_records(sample, root, skip_media)
        errors.extend(record_errors)
    return errors, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--skip-media", action="store_true", help="skip referenced-file existence checks")
    args = parser.parse_args()
    errors, count = validate_dataset(args.dataset.resolve(), args.skip_media)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"INVALID: {len(errors)} error(s), {count} record(s)", file=sys.stderr)
        return 1
    print(f"VALID: {count} record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
