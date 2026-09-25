"""Project SE audio roles without requiring transcription annotations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def project_se_samples(
    *, sample_jsonl_path: Path, raw_dir: Path, language: str,
    dataset_label: str, metadata_base: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()
    count = 0
    with sample_jsonl_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            count += 1
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("SE sample must be an object")
                attr = record.get("attribute") if isinstance(record.get("attribute"), dict) else {}
                noisy = record.get("noisy_audio") or attr.get("path") or record.get("audio") or record.get("path")
                reference = record.get("reference_audio")
                paths: dict[str, str] = {}
                for role, value in (("noisy_audio", noisy), ("reference_audio", reference)):
                    if role == "reference_audio" and value is None:
                        # No-reference routes may score noisy-only datasets.
                        continue
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(f"missing SE {role} path")
                    path = Path(value).expanduser()
                    if not path.is_absolute():
                        path = raw_dir / path
                    path = path.resolve()
                    if not path.is_file() or path.stat().st_size == 0:
                        raise ValueError(f"SE {role} not found or empty: {path}")
                    paths[role] = str(path)
                if attr.get("size") is not None and int(attr["size"]) != Path(paths["noisy_audio"]).stat().st_size:
                    raise ValueError("SE noisy audio size mismatch")
                key = str(record.get("key") or record.get("sample_id") or Path(paths["noisy_audio"]).stem).strip()
                if not key or any(char in key for char in "\t\r\n"):
                    raise ValueError("SE key must be a nonempty single-line identifier")
                if key in seen:
                    raise ValueError(f"duplicate SE key: {key}")
                seen.add(key)
                rows.append({
                    "key": key, "path": paths["noisy_audio"], **paths,
                    "task": "SE", "language": str(record.get("language") or language or "any"),
                    "dataset": dataset_label,
                    "sample_rate": record.get("sample_rate") or attr.get("sample_rate"),
                    "metadata": {**metadata_base, "sample_id": record.get("sample_id") or key},
                })
            except (TypeError, ValueError, OSError) as exc:
                skipped.append({"line": line_number, "reason": str(exc)})
    return rows, skipped, count
