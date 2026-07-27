#!/usr/bin/env python3
"""Import resolved prediction artifacts into a fresh re-evaluation run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.config import Config
from sure_eval.datasets import DatasetManager


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def _prediction_map(path: Path) -> dict[str, str]:
    predictions: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not line.strip():
                continue
            if "\t" in line:
                key, value = line.split("\t", 1)
            else:
                parts = line.split(None, 1)
                key = parts[0]
                value = parts[1] if len(parts) > 1 else ""
            predictions[key] = value
    return predictions


def _structured_map(path: Path | None) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if path is None or not path.is_file():
        return rows
    for row in _read_jsonl(path):
        key = str(row.get("key") or "")
        if key:
            rows[key] = row
    return rows


def _copy_or_link(source: Path, dest: Path, mode: str) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    if mode == "hardlink":
        try:
            os.link(source, dest)
            return "hardlink"
        except OSError:
            shutil.copy2(source, dest)
            return "copy_hardlink_fallback"
    shutil.copy2(source, dest)
    return "copy"


def _dataset_samples(manager: DatasetManager, dataset: str) -> tuple[Path, list[dict[str, Any]]]:
    canonical = manager.normalize_dataset_name(dataset)
    jsonl_path = manager.get_jsonl_path(canonical)
    if not jsonl_path.exists():
        jsonl_path = manager.download_and_convert(canonical)
    return jsonl_path, _read_jsonl(jsonl_path)


def _filter_import(
    *,
    dataset: str,
    task: str,
    language: str,
    source_txt: Path,
    source_jsonl: Path | None,
    dest_txt: Path,
    dest_jsonl: Path,
    samples: list[dict[str, Any]],
    max_samples: int,
) -> int:
    selected = samples[:max_samples] if max_samples > 0 else samples
    predictions = _prediction_map(source_txt)
    structured = _structured_map(source_jsonl)
    txt_rows: list[str] = []
    jsonl_rows: list[dict[str, Any]] = []
    for sample in selected:
        key = str(sample.get("key") or "")
        if not key:
            continue
        value = predictions.get(key, "")
        txt_rows.append(f"{key}\t{value}")
        row = dict(structured.get(key) or {})
        if not row:
            row = {
                "key": key,
                "dataset": dataset,
                "task": task or sample.get("task"),
                "language": language or sample.get("language"),
                "prediction": {"text": value},
                "normalized_prediction": value,
                "raw_response": None,
            }
        row["key"] = key
        row.setdefault("dataset", dataset)
        row.setdefault("task", task or sample.get("task"))
        row.setdefault("language", language or sample.get("language"))
        jsonl_rows.append(row)
    dest_txt.parent.mkdir(parents=True, exist_ok=True)
    dest_txt.write_text("\n".join(txt_rows) + ("\n" if txt_rows else ""), encoding="utf-8")
    _write_jsonl(dest_jsonl, jsonl_rows)
    return len(txt_rows)


def import_predictions(args: argparse.Namespace) -> dict[str, Any]:
    source = _read_json(Path(args.source_resolved))
    run_dir = Path(args.run_dir).expanduser().resolve()
    pred_dir = run_dir / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config.from_yaml(args.config) if args.config else Config.from_env()
    manager = DatasetManager(cfg)
    max_samples = max(0, int(args.max_samples or 0))

    imported: list[dict[str, Any]] = []
    for item in source.get("predictions") or []:
        if not isinstance(item, dict):
            continue
        dataset = str(item.get("dataset") or "")
        source_txt = Path(str(item.get("txt") or ""))
        source_jsonl = Path(str(item.get("jsonl") or "")) if item.get("jsonl") else None
        if not dataset or not source_txt.is_file():
            raise FileNotFoundError(f"missing source prediction txt for dataset={dataset}: {source_txt}")
        jsonl_path, samples = _dataset_samples(manager, dataset)
        task = str(item.get("task") or (samples[0].get("task") if samples else ""))
        language = str(item.get("language") or (samples[0].get("language") if samples else ""))
        dest_txt = pred_dir / f"{dataset}.txt"
        dest_jsonl = pred_dir / f"{dataset}.jsonl"
        filtered = max_samples > 0 or source_jsonl is None or not source_jsonl.is_file()
        if filtered:
            copy_mode_actual = "filtered_copy" if max_samples > 0 else "synthesized_jsonl_copy"
            imported_samples = _filter_import(
                dataset=dataset,
                task=task,
                language=language,
                source_txt=source_txt,
                source_jsonl=source_jsonl,
                dest_txt=dest_txt,
                dest_jsonl=dest_jsonl,
                samples=samples,
                max_samples=max_samples,
            )
        else:
            copy_mode_actual = _copy_or_link(source_txt, dest_txt, args.copy_mode)
            _copy_or_link(source_jsonl, dest_jsonl, args.copy_mode)
            imported_samples = _count_lines(dest_txt)
        imported.append(
            {
                "dataset": dataset,
                "task": task,
                "language": language,
                "dataset_jsonl_path": str(jsonl_path),
                "source_txt": str(source_txt),
                "source_jsonl": str(source_jsonl) if source_jsonl else "",
                "dest_txt": str(dest_txt),
                "dest_jsonl": str(dest_jsonl),
                "copy_mode": copy_mode_actual,
                "filtered": filtered,
                "max_samples": max_samples if max_samples > 0 else None,
                "dataset_total_samples": len(samples),
                "imported_samples": imported_samples,
                "source_txt_sha256": _sha256(source_txt),
                "dest_txt_sha256": _sha256(dest_txt),
                "source_jsonl_sha256": _sha256(source_jsonl) if source_jsonl and source_jsonl.is_file() else None,
                "dest_jsonl_sha256": _sha256(dest_jsonl),
            }
        )

    manifest = {
        "schema": "sure.reval.prediction_reuse_manifest.v1",
        "generated_at": _utc_now(),
        "run_dir": str(run_dir),
        "source": {
            "source": source.get("source"),
            "source_kind": source.get("source_kind"),
            "source_predictions_dir": source.get("source_predictions_dir"),
            "source_run_dir": source.get("source_run_dir"),
            "source_results_dir": source.get("source_results_dir"),
            "source_run_id": source.get("source_run_id"),
            "old_evaluation_reused": False,
        },
        "predictions_dir": str(pred_dir),
        "datasets": [item["dataset"] for item in imported],
        "copy_mode_requested": args.copy_mode,
        "max_samples": max_samples if max_samples > 0 else None,
        "imported": imported,
    }
    output = Path(args.output) if args.output else run_dir / "prediction_reuse_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Import existing prediction files into a fresh reval run")
    parser.add_argument("--source-resolved", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--copy-mode", choices=("copy", "hardlink"), default="copy")
    parser.add_argument("--max-samples", type=int, default=0)
    parser.add_argument("--config")
    parser.add_argument("--output")
    args = parser.parse_args()

    manifest = import_predictions(args)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
