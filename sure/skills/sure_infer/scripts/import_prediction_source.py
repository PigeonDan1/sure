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


def _write_standard_prediction_manifests(
    *,
    run_dir: Path,
    pred_dir: Path,
    imported: list[dict[str, Any]],
    source: dict[str, Any],
) -> tuple[Path, Path]:
    generated_at = _utc_now()
    datasets: list[dict[str, Any]] = []
    conversions: list[dict[str, Any]] = []
    for item in imported:
        dataset = str(item.get("dataset") or "")
        dest_txt = Path(str(item.get("dest_txt") or ""))
        dest_jsonl = Path(str(item.get("dest_jsonl") or ""))
        jsonl_exists = dest_jsonl.is_file()
        datasets.append(
            {
                "dataset": dataset,
                "task": item.get("task"),
                "language": item.get("language"),
                "format_used": "jsonl+txt" if jsonl_exists else "txt",
                "txt": str(dest_txt),
                "jsonl": str(dest_jsonl) if jsonl_exists else None,
                "txt_sha256": _sha256(dest_txt) if dest_txt.is_file() else None,
                "jsonl_sha256": _sha256(dest_jsonl) if jsonl_exists else None,
                "num_rows": _count_lines(dest_txt),
                "structured_num_rows": _count_lines(dest_jsonl) if jsonl_exists else 0,
                "source_txt": item.get("source_txt"),
                "source_jsonl": item.get("source_jsonl"),
                "copy_mode": item.get("copy_mode"),
                "filtered": item.get("filtered"),
                "max_samples": item.get("max_samples"),
            }
        )
        steps = [
            {
                "name": "source_prediction_reuse",
                "input": item.get("source_txt"),
                "output": str(dest_txt),
                "script": "scripts/import_prediction_source.py",
            }
        ]
        if item.get("filtered"):
            steps.append(
                {
                    "name": "bounded_filter_or_structured_synthesis",
                    "input": item.get("source_jsonl") or item.get("source_txt"),
                    "output": str(dest_jsonl),
                    "script": "scripts/import_prediction_source.py:_filter_import",
                }
            )
        else:
            steps.append(
                {
                    "name": "structured_prediction_reuse",
                    "input": item.get("source_jsonl"),
                    "output": str(dest_jsonl),
                    "script": "scripts/import_prediction_source.py",
                }
            )
        conversions.append(
            {
                "dataset": dataset,
                "source_format": "existing_sure_predictions",
                "format_used": "jsonl+txt" if jsonl_exists else "txt",
                "num_rows": _count_lines(dest_txt),
                "source_artifacts": {
                    "source_txt": item.get("source_txt"),
                    "source_jsonl": item.get("source_jsonl"),
                    "compatibility_tsv": str(dest_txt),
                    "structured_jsonl": str(dest_jsonl) if jsonl_exists else None,
                },
                "steps": steps,
                "conversion_trace": None,
            }
        )
    manifest = {
        "schema": "sure.eval.prediction_manifest.v1",
        "generated_at": generated_at,
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "model_name": str(source.get("model_name") or ""),
        "tool_name": str(source.get("model_name") or ""),
        "predictions_dir": str(pred_dir),
        "datasets": datasets,
    }
    conversion_manifest = {
        "schema": "sure.eval.prediction_conversion_manifest.v1",
        "generated_at": generated_at,
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "generated_by": "scripts/import_prediction_source.py",
        "predictions_dir": str(pred_dir),
        "datasets": conversions,
    }
    manifest_path = pred_dir / "manifest.json"
    conversion_path = pred_dir / "conversion_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    conversion_path.write_text(json.dumps(conversion_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest_path, conversion_path


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


def _link_or_copy_evidence(source: Path, dest: Path) -> dict[str, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    try:
        relative = os.path.relpath(source, start=dest.parent)
        dest.symlink_to(relative)
        return {"mode": "symlink", "path": str(dest), "target": relative, "source": str(source)}
    except OSError:
        shutil.copy2(source, dest)
        return {"mode": "copy", "path": str(dest), "target": str(source), "source": str(source)}


def _write_source_provenance_links(run_dir: Path, source: dict[str, Any]) -> dict[str, Any]:
    provenance = source.get("source_inference_provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    if source.get("source_protocol"):
        provenance["source_protocol"] = source["source_protocol"]
    mapping = {
        "source_protocol": "source_protocol.yaml",
        "source_prediction_generation_status": "source_prediction_generation_status.json",
        "source_runtime_inventory": "source_runtime_inventory.json",
        "source_runtime_links_manifest": "source_runtime_links_manifest.json",
    }
    links: dict[str, Any] = {}
    links_dir = run_dir / "provenance"
    for key, filename in mapping.items():
        raw = provenance.get(key)
        path = Path(str(raw)).expanduser() if raw else None
        if path and path.is_file():
            links[key] = _link_or_copy_evidence(path.resolve(), links_dir / filename)
    manifest = {
        "schema": "sure.reval.source_inference_provenance.v1",
        "generated_at": _utc_now(),
        "policy": {
            "generation_policy": "reused_predictions_no_inference",
            "links_checkpoint_payloads": False,
            "old_evaluation_reused": False,
        },
        "source_inference_provenance": provenance,
        "links_dir": str(links_dir),
        "links": links,
        "inference_unknown": not bool(links),
    }
    output = run_dir / "source_inference_provenance.json"
    output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


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

    prediction_manifest_path, conversion_manifest_path = _write_standard_prediction_manifests(
        run_dir=run_dir,
        pred_dir=pred_dir,
        imported=imported,
        source=source,
    )
    provenance_manifest = _write_source_provenance_links(run_dir, source)
    manifest = {
        "schema": "sure.reval.prediction_reuse_manifest.v1",
        "generated_at": _utc_now(),
        "run_dir": str(run_dir),
        "source": {
            "source": source.get("source_results_dir"),
            "source_kind": source.get("source_kind"),
            "source_predictions_dir": source.get("source_predictions_dir"),
            "source_run_dir": source.get("source_run_dir"),
            "source_results_dir": source.get("source_results_dir"),
            "source_run_id": source.get("source_run_id"),
            "source_inference_provenance": source.get("source_inference_provenance"),
            "old_evaluation_reused": False,
        },
        "source_inference_provenance_manifest": str(run_dir / "source_inference_provenance.json"),
        "source_inference_provenance": provenance_manifest,
        "predictions_dir": str(pred_dir),
        "prediction_manifest": str(prediction_manifest_path),
        "conversion_manifest": str(conversion_manifest_path),
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
