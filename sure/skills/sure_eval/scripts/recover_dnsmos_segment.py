#!/usr/bin/env python3
"""Recover a DNSMOS TTS segment with resumable per-sample scoring.

This is a deterministic recovery path for VC images that terminate long
node-local DNSMOS batch jobs without a Python traceback. It still uses the
sure-evaluation DNSMOS provider and score_mos_metric aggregation.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def _safe_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in value.lower()) or "metric"


def _load_raw_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = str(row.get("key") or "")
        result = row.get("result")
        if key and isinstance(result, dict):
            rows[key] = result
    return rows


def _strict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return _strict(dataclasses.asdict(obj))
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(key): _strict(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_strict(value) for value in obj]
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return obj
    return obj


class _PrecomputedDNSMOSProvider:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self._rows = rows

    def score_batch(self, rows: list[tuple[str, str]], *, metric_name: str) -> list[dict[str, Any]]:
        del metric_name
        return [self._rows[key] for key, _prediction in rows]


def _sample_report_row(sample: dict[str, Any], *, dataset: str, metric: str) -> dict[str, Any]:
    return {
        "key": sample["sample_id"],
        "dataset": dataset,
        "task": "TTS",
        "metric": metric,
        "prediction": sample["prediction_audio"],
        "generated_audio": sample["prediction_audio"],
        "reference_text": sample.get("reference_text", ""),
        "reference_audio": sample.get("reference_audio", ""),
    }


def _pipeline_description(*, language: str, metric: str) -> dict[str, Any]:
    node_id = "scoring/dnsmos"
    return {
        "task": "TTS",
        "pipeline_id": f"tts.{language}.multi.audio_metric_nodes",
        "route_id": f"tts.{language}.multi.audio_metric_nodes",
        "metric": metric,
        "language": language,
        "node_ids": [node_id],
        "required_roles": ["prediction_audio"],
        "optional_roles": [],
        "output_dir_required": True,
        "contracts": [
            {
                "purpose": "tts_audio_quality",
                "required_roles": ["prediction_audio"],
                "row_format": "generated_audio_rows",
                "alignment_key": "sample_id",
                "aggregation": "mean",
                "main_report": True,
                "metric_id": node_id,
            }
        ],
        "task_config_path": "src/sure_eval/evaluation/tasks/tts/manifest.yaml",
        "node_config_paths": ["src/sure_eval/evaluation/nodes/scoring/dnsmos/manifest.yaml"],
        "nodes": [
            {
                "node_id": node_id,
                "stage": "scoring",
                "version": "v1",
                "manifest_path": "src/sure_eval/evaluation/nodes/scoring/dnsmos/manifest.yaml",
            }
        ],
        "conversion_steps": [],
    }


def _write_protocol(path: Path, *, dataset: str, metric: str, language: str, model_dir: str, tool_name: str, protocol_id: str) -> None:
    payload = {
        "schema": "sure.eval.protocol.v1",
        "protocol_id": protocol_id,
        "model": {"model_dir": model_dir, "tool_name": tool_name, "server": {}},
        "datasets": [
            {
                "name": dataset,
                "task": "TTS",
                "language": language,
                "metrics": [metric],
            }
        ],
        "protocol": {"id": protocol_id},
        "artifact_layout": {
            "evaluation_payload": "evaluation_payload.json",
            "report_jsonl": "report.jsonl",
            "protocol": "protocol.yaml",
            "metrics": "metrics/<dataset>/<metric_slug>/",
            "sample_reports": "sample_reports/<dataset>/<metric_slug>.jsonl",
        },
    }
    try:
        import yaml

        text = yaml.safe_dump(_strict(payload), allow_unicode=True, sort_keys=False)
    except Exception:
        text = json.dumps(_strict(payload), ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--tool-name", required=True)
    parser.add_argument("--protocol-id", default="strict_core")
    parser.add_argument("--progress-interval", type=int, default=10)
    args = parser.parse_args()

    engine_root = args.engine_root.resolve()
    sys.path.insert(0, str(engine_root / "src"))

    from sure_eval.evaluation.nodes.scoring._audio_quality_dispatch import score_mos_metric
    from sure_eval.evaluation.nodes.scoring.common.mos_providers import DNSMOSProvider

    run_dir = args.run_dir.resolve()
    metric = "dnsmos"
    slug = _safe_component(metric)
    segment_dir = run_dir / "evaluation_segments" / "segment_tts_mos_dnsmos"
    external_dir = segment_dir / "results" / "evaluation_runs" / args.dataset / metric
    samples_path = external_dir / "samples.jsonl"
    if not samples_path.exists():
        raise FileNotFoundError(samples_path)

    samples = _read_jsonl(samples_path)
    if not samples:
        raise RuntimeError(f"empty samples file: {samples_path}")
    language = str(samples[0].get("language") or "en")
    raw_rows_path = external_dir / "dnsmos_raw_rows.jsonl"
    raw_rows = _load_raw_rows(raw_rows_path)

    provider = DNSMOSProvider(cache_dir=engine_root / "src" / "sure_eval" / "evaluation" / "nodes" / "scoring" / "dnsmos" / "checkpoints")
    total = len(samples)
    scored_this_run = 0
    for index, sample in enumerate(samples, start=1):
        key = str(sample["sample_id"])
        if key in raw_rows:
            continue
        result = dict(provider(str(sample["prediction_audio"])))
        raw_rows[key] = result
        _append_jsonl(raw_rows_path, {"key": key, "result": result})
        scored_this_run += 1
        if scored_this_run == 1 or scored_this_run % max(args.progress_interval, 1) == 0:
            print(f"dnsmos_progress {len(raw_rows)}/{total}", flush=True)

    if len(raw_rows) != total:
        print(f"dnsmos_partial {len(raw_rows)}/{total}; rerun to resume", flush=True)
        return 3

    mos_rows = [(str(sample["sample_id"]), str(sample["prediction_audio"])) for sample in samples]
    trace = score_mos_metric(mos_rows, metric_name=metric, provider=_PrecomputedDNSMOSProvider(raw_rows))
    result = dict(trace.details["result"])
    score = float(result["score"])
    pipeline = _pipeline_description(language=language, metric=metric)
    rows = []
    per_sample = result.get("per_sample") if isinstance(result.get("per_sample"), list) else []
    for sample, sample_result in zip(samples, per_sample):
        row = dict(sample)
        row.setdefault("metadata", {})["dataset"] = args.dataset
        row.setdefault("metadata", {})["task"] = "TTS"
        row.setdefault("mos", {})[metric] = sample_result
        rows.append(row)

    report = {
        "task": "TTS",
        "language": language,
        "metric": metric,
        "score": score,
        "pipeline_id": pipeline["pipeline_id"],
        "pipeline_trace": [_strict(trace)],
        "input_contract": None,
        "input_files": {"prediction_audio": "batch", "reference_text": "inline", "reference_audio": "batch"},
        "details": {
            "results": {metric: result},
            "rows": rows,
            "input_contract": {},
            "input_files": {"prediction_audio": "batch", "reference_text": "inline", "reference_audio": "batch"},
        },
    }

    metric_dir = segment_dir / "metrics" / args.dataset / slug
    sample_report_path = segment_dir / "sample_reports" / args.dataset / f"{slug}.jsonl"
    for destination in (external_dir / "report.json", metric_dir / "report.json"):
        _write_json(destination, report)
    for destination in (external_dir / "pipeline_description.json", metric_dir / "pipeline_description.json"):
        _write_json(destination, pipeline)

    sample_report_path.parent.mkdir(parents=True, exist_ok=True)
    with sample_report_path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(_sample_report_row(sample, dataset=args.dataset, metric=metric), ensure_ascii=False) + "\n")

    prediction_path = run_dir / "predictions" / f"{args.dataset}.txt"
    dataset_jsonl = Path.cwd() / "data" / "datasets" / "sure_benchmark" / "jsonl" / f"{args.dataset}.jsonl"
    row = {
        "schema": "sure.eval.payload.dataset_metric.v2",
        "dataset": args.dataset,
        "task": "TTS",
        "language": language,
        "metric": metric,
        "pipeline_id": pipeline["pipeline_id"],
        "route_id": pipeline["route_id"],
        "nodes": ["scoring/dnsmos"],
        "node_config_paths": ["src/sure_eval/evaluation/nodes/scoring/dnsmos/manifest.yaml"],
        "evaluation_backend": "external",
        "evaluator_version": "sure-evaluation",
        "num_samples": total,
        "rps": {"status": "missing_baseline", "dataset": args.dataset, "score": score},
        "evaluation_context": {
            "backend": "sure-evaluation",
            "engine_source": "submodule",
            "engine_root": str(engine_root),
            "pipeline_id": pipeline["pipeline_id"],
            "route_id": pipeline["route_id"],
            "nodes": ["scoring/dnsmos"],
            "node_config_paths": ["src/sure_eval/evaluation/nodes/scoring/dnsmos/manifest.yaml"],
            "external_output_dir": str(external_dir),
            "samples_jsonl": str(samples_path),
            "requested_metric_source": "cli_override",
            "recovery": "recover_dnsmos_segment.py",
        },
        "result": {
            "metric_name": metric,
            "score": score,
            "score_key": "OVRL",
            "OVRL": score,
            "mos": score,
        },
        "pipeline": {
            "pipeline_id": pipeline["pipeline_id"],
            "route_id": pipeline["route_id"],
            "nodes": ["scoring/dnsmos"],
            "conversion_steps": [],
            "report_path": str(metric_dir / "report.json"),
            "description_path": str(metric_dir / "pipeline_description.json"),
        },
        "inputs": {"jsonl_path": str(dataset_jsonl), "prediction_path": str(prediction_path)},
        "artifacts": {
            "metric_artifact_dir": str(metric_dir),
            "report": str(metric_dir / "report.json"),
            "pipeline_description": str(metric_dir / "pipeline_description.json"),
            "sample_report": str(sample_report_path),
            "prediction_file": str(prediction_path),
        },
        "run_id": segment_dir.name,
        "tool_uid": args.tool_name,
        "protocol_id": args.protocol_id,
    }
    payload = {
        "schema": "sure.eval.payload.v2",
        "evaluation_backend": "external",
        "external_engine": {"source": "submodule", "engine_root": str(engine_root)},
        "results": [row],
    }
    _write_json(segment_dir / "evaluation_payload.json", payload)
    (segment_dir / "report.jsonl").write_text(json.dumps(_strict(row), ensure_ascii=False) + "\n", encoding="utf-8")
    _write_protocol(
        segment_dir / "protocol.yaml",
        dataset=args.dataset,
        metric=metric,
        language=language,
        model_dir=args.model_dir,
        tool_name=args.tool_name,
        protocol_id=args.protocol_id,
    )
    _write_json(
        segment_dir / "evaluation_only_status.json",
        {
            "run_id": run_dir.name,
            "status": "completed",
            "execution_surface": "audio_evaluation_only",
            "audio_eval_mode": "segment",
            "audio_eval_segment": "segment_tts_mos_dnsmos",
            "datasets": args.dataset,
            "metrics": metric,
            "segment_payload": str(segment_dir / "evaluation_payload.json"),
            "recovery": "recover_dnsmos_segment.py",
        },
    )
    print(f"dnsmos_completed {args.dataset} {score}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
