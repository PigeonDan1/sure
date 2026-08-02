#!/usr/bin/env python3
"""Resolve an existing prediction source for /sure_reval.

The source may be:
- a canonical model eval run directory containing predictions/
- a compatibility results/<model>/<protocol>/ directory containing predictions/
- a bare predictions/ directory

This script only discovers immutable prediction inputs. It never treats old
evaluation reports, metric artifacts, or sample reports as reusable outputs.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from dataset_alias import resolve_dataset_alias


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _split_values(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for item in str(value).replace(",", " ").split():
            item = item.strip()
            if item and item not in out:
                out.append(item)
    return out


def _source_kind(source: Path) -> tuple[str, Path]:
    if source.name == "predictions":
        return "predictions_dir", source
    if (source / "predictions").is_dir():
        if (source / "evaluation_payload.json").is_file() or (source / "main_agent_run_report.json").is_file():
            return "run_dir", source / "predictions"
        return "results_dir", source / "predictions"
    raise FileNotFoundError(f"source does not contain a predictions directory: {source}")


def _prediction_files(predictions_dir: Path) -> dict[str, dict[str, str | None]]:
    files: dict[str, dict[str, str | None]] = {}
    for txt in sorted(predictions_dir.glob("*.txt")):
        dataset = txt.stem
        files.setdefault(dataset, {"txt": None, "jsonl": None})["txt"] = str(txt.resolve())
    for jsonl in sorted(predictions_dir.glob("*.jsonl")):
        dataset = jsonl.stem
        files.setdefault(dataset, {"txt": None, "jsonl": None})["jsonl"] = str(jsonl.resolve())
    return files


def _resolve_relative_path(value: str, anchors: list[Path]) -> str:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path.resolve()) if path.exists() else str(path)
    for anchor in anchors:
        candidate = anchor / path
        if candidate.exists():
            return str(candidate.resolve())
    return value


def _infer_from_report(source: Path) -> dict[str, Any]:
    rows = _read_jsonl(source / "report.jsonl")
    if not rows:
        return {}
    first = rows[0]
    run = first.get("run") if isinstance(first.get("run"), dict) else {}
    model = first.get("model") if isinstance(first.get("model"), dict) else {}
    dataset_rows: list[dict[str, Any]] = []
    anchors = [source, *source.parents]
    for row in rows:
        dataset = row.get("dataset") if isinstance(row.get("dataset"), dict) else {}
        prediction = row.get("prediction") if isinstance(row.get("prediction"), dict) else {}
        prediction_file = prediction.get("file") if isinstance(prediction.get("file"), str) else ""
        dataset_rows.append(
            {
                "name": dataset.get("name") or row.get("dataset"),
                "task": dataset.get("task") or row.get("task"),
                "language": dataset.get("language") or row.get("language"),
                "metric": (row.get("metric") or {}).get("name") if isinstance(row.get("metric"), dict) else row.get("metric"),
                "prediction_file": _resolve_relative_path(prediction_file, anchors) if prediction_file else "",
            }
        )
    prediction_files = [Path(str(item.get("prediction_file"))) for item in dataset_rows if item.get("prediction_file")]
    canonical_run_dir = ""
    for prediction_file in prediction_files:
        if prediction_file.parent.name == "predictions":
            canonical_run_dir = str(prediction_file.parent.parent)
            break
    return {
        "run_id": run.get("run_id") or first.get("run_id"),
        "protocol_id": run.get("protocol_id") or first.get("protocol_id"),
        "model_name": model.get("model_name") or first.get("tool_uid"),
        "model_dir": _resolve_relative_path(str(model.get("model_dir") or ""), anchors) if model.get("model_dir") else "",
        "canonical_run_dir": canonical_run_dir,
        "datasets": [item for item in dataset_rows if item.get("name")],
    }


def _infer_from_protocol(source: Path) -> dict[str, Any]:
    protocol = _read_yaml(source / "protocol.yaml")
    if not protocol:
        return {}
    model = protocol.get("model") if isinstance(protocol.get("model"), dict) else {}
    run = protocol.get("run") if isinstance(protocol.get("run"), dict) else {}
    datasets = protocol.get("datasets") if isinstance(protocol.get("datasets"), list) else []
    return {
        "run_id": run.get("run_id"),
        "protocol_id": protocol.get("protocol_id"),
        "model_name": model.get("tool_name") or source.parent.name,
        "model_dir": model.get("model_dir") or "",
        "canonical_run_dir": run.get("run_dir") or "",
        "datasets": [item for item in datasets if isinstance(item, dict)],
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source).expanduser().resolve()
    kind, predictions_dir = _source_kind(source)
    discovered = _prediction_files(predictions_dir)
    if not discovered:
        raise FileNotFoundError(f"no prediction files found under {predictions_dir}")

    report_info = _infer_from_report(source if kind != "predictions_dir" else predictions_dir.parent)
    protocol_info = _infer_from_protocol(source if kind != "predictions_dir" else predictions_dir.parent)
    requested_datasets = _split_values(args.datasets)
    selected_datasets = requested_datasets or [
        str(item.get("name"))
        for item in (protocol_info.get("datasets") or report_info.get("datasets") or [])
        if item.get("name")
    ] or sorted(discovered)
    # Accept the same short dataset aliases /sure_eval accepts (e.g.
    # "aishell1" for predictions actually saved as
    # "aishell1__v1.0.2__asr.txt"). A name already present in `discovered`
    # (including any name /sure_eval could not itself have expanded) passes
    # through unchanged; only a short alias with a unique versioned match
    # gets rewritten. Unmatched or ambiguous names are left as-is so the
    # existing missing-file check below still reports them.
    selected_datasets = [resolve_dataset_alias(dataset, discovered) or dataset for dataset in selected_datasets]

    missing = [dataset for dataset in selected_datasets if dataset not in discovered or not discovered[dataset].get("txt")]
    if missing:
        raise FileNotFoundError(f"missing prediction txt file(s) for dataset(s): {', '.join(missing)}")

    dataset_meta: dict[str, dict[str, Any]] = {}
    for item in (protocol_info.get("datasets") or []) + (report_info.get("datasets") or []):
        if isinstance(item, dict) and item.get("name"):
            dataset_meta.setdefault(str(item["name"]), item)

    model_name = args.model or protocol_info.get("model_name") or report_info.get("model_name") or (
        source.parent.name if kind == "results_dir" else ""
    )
    model_dir = args.model_dir or protocol_info.get("model_dir") or report_info.get("model_dir") or ""
    canonical_run_dir = protocol_info.get("canonical_run_dir") or report_info.get("canonical_run_dir") or (
        str(source) if kind == "run_dir" else ""
    )

    predictions = []
    for dataset in selected_datasets:
        meta = dataset_meta.get(dataset, {})
        files = discovered[dataset]
        predictions.append(
            {
                "dataset": dataset,
                "task": meta.get("task"),
                "language": meta.get("language"),
                "metrics": meta.get("metrics") or ([meta.get("metric")] if meta.get("metric") else []),
                "txt": files.get("txt"),
                "jsonl": files.get("jsonl"),
            }
        )

    return {
        "schema": "sure.reval.prediction_source_resolved.v1",
        "generated_at": _utc_now(),
        "source": str(source),
        "source_kind": kind,
        "source_predictions_dir": str(predictions_dir.resolve()),
        "source_run_dir": canonical_run_dir,
        "source_results_dir": str(source) if kind == "results_dir" else "",
        "source_run_id": protocol_info.get("run_id") or report_info.get("run_id") or "",
        "protocol_id": args.protocol_id or protocol_info.get("protocol_id") or report_info.get("protocol_id") or "strict_core",
        "model_name": model_name,
        "model_dir": model_dir,
        "datasets": selected_datasets,
        "predictions": predictions,
        "old_evaluation_reused": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve an existing SURE prediction source")
    parser.add_argument("--source", required=True)
    parser.add_argument("--model")
    parser.add_argument("--model-dir")
    parser.add_argument("--datasets", nargs="*")
    parser.add_argument("--protocol-id")
    parser.add_argument("--output")
    args = parser.parse_args()

    payload = build_payload(args)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
