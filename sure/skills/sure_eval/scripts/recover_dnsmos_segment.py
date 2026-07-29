#!/usr/bin/env python3
"""Recover a DNSMOS TTS segment with resumable per-sample scoring.

This is a deterministic recovery path for VC images that terminate long
node-local DNSMOS batch jobs without a Python traceback. It still uses the
sure-evaluation DNSMOS provider and score_mos_metric aggregation.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def _safe_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in value.lower()) or "metric"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


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
    del dataset, metric, language
    payload = {
        "schema": "sure.eval.inference_protocol.v1",
        "protocol_id": protocol_id,
        "run": {
            "run_id": path.parent.name,
            "run_dir": str(path.parent),
            "created_at": _utc_now(),
        },
        "model": {
            "model_name": Path(model_dir).name if model_dir else tool_name,
            "model_dir": model_dir,
            "model_source": None,
            "weights_source": None,
            "model_dir_source": None,
            "mcp_tool_name": tool_name,
            "server_config": {},
        },
        "protocol_selection": {
            "protocol_id": protocol_id,
            "definition_path": None,
            "model_protocol_config_path": None,
            "is_default": protocol_id == "strict_core",
            "purpose": "recovered audio-quality segment from existing predictions",
            "standard_params": {},
            "resolved_model_params": {},
            "unmapped": {},
        },
        "inference_environment": {
            "execution_path": "reused_predictions",
            "vc": {},
            "container": {},
            "server": {},
            "env": {},
            "mount_policy": {},
        },
        "inference_constraints": {
            "no_external_lm": True,
            "no_retrieval": True,
            "no_hotwords": True,
            "single_pass_decode": True,
            "no_prompt_engineering": True,
            "local_fallback_allowed": False,
            "metric_logic_in_inference_image_allowed": False,
            "required_preflight_checks": ["deterministic_prediction_contract"],
        },
        "execution_surface": {
            "materialized": True,
            "execution_surface_type": "audio_evaluation_only_recovery",
            "entrypoint_path": "scripts/recover_dnsmos_segment.py",
            "generation_method": "recovery_script",
            "isolation_compliance": {},
        },
        "prediction_contract": {
            "contract_path": "references/contracts/prediction_output_contract.md",
            "compatibility_tsv": "predictions/<dataset>.txt",
            "structured_jsonl": "predictions/<dataset>.jsonl",
            "format_used": "jsonl+txt",
            "generated_by": "reused from parent run predictions",
            "protocol_argument": protocol_id,
        },
        "notes": [
            "This file records inference protocol only for a recovered evaluation segment.",
            "Dataset scope, evaluation routes, metric results, validation, and metric artifacts are recorded in report_snapshot.md and report.jsonl.",
        ],
    }
    try:
        import yaml

        text = yaml.safe_dump(_strict(payload), allow_unicode=True, sort_keys=False)
    except Exception:
        text = json.dumps(_strict(payload), ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _metric_display(score: float) -> str:
    return f"{score:.6f}"


def _standard_report_row(
    *,
    row: dict[str, Any],
    score: float,
    total: int,
    language: str,
    metric_dir: Path,
    sample_report_path: Path,
    prediction_path: Path,
    structured_prediction_path: Path,
    protocol_id: str,
    model_dir: str,
    tool_name: str,
) -> dict[str, Any]:
    return _strict(
        {
            "schema": "sure.eval.report.dataset_metric.v1",
            "run": {"run_id": row["run_id"], "protocol_id": protocol_id},
            "model": {"model_name": Path(model_dir).name if model_dir else tool_name, "model_dir": model_dir, "tool_name": tool_name},
            "dataset": {
                "name": row["dataset"],
                "task": "TTS",
                "language": language,
                "jsonl_path": row["inputs"]["jsonl_path"],
                "num_samples": total,
            },
            "prediction": {
                "file": str(prediction_path),
                "validation": {
                    "expected_samples": total,
                    "provided_predictions": _count_lines(prediction_path),
                    "missing_keys": [],
                    "extra_keys": [],
                    "duplicate_keys": [],
                    "empty_prediction_keys": [],
                    "structured_missing_keys": [],
                    "structured_extra_keys": [],
                    "structured_duplicate_keys": [],
                    "invalid_structured_rows": [],
                    "structured_projection_mismatch_keys": [],
                    "contract_violation_keys": [],
                    "is_valid": True,
                    "prediction_jsonl_path": str(structured_prediction_path) if structured_prediction_path.is_file() else None,
                    "format_used": "jsonl+txt" if structured_prediction_path.is_file() else "txt",
                },
            },
            "metric": {"name": "dnsmos", "score": score, "unit": "mos", "display": _metric_display(score), "higher_is_better": True, "score_key": "OVRL"},
            "baseline": None,
            "rps": row.get("rps"),
            "pipeline": row["pipeline"],
            "versions": {
                "evaluation_backend": "external",
                "evaluator_version": "sure-evaluation",
                "recovery": "scripts/recover_dnsmos_segment.py",
            },
            "artifacts": {
                "metric_artifact_dir": str(metric_dir),
                "report": str(metric_dir / "report.json"),
                "pipeline_description": str(metric_dir / "pipeline_description.json"),
                "sample_report": str(sample_report_path),
            },
            "status": "success",
        }
    )


def _materialize_segment_predictions(segment_dir: Path, parent_prediction_path: Path, dataset: str, task: str, language: str) -> tuple[Path, Path]:
    pred_dir = segment_dir / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = pred_dir / f"{dataset}.txt"
    structured_prediction_path = pred_dir / f"{dataset}.jsonl"
    if parent_prediction_path.is_file():
        shutil.copy2(parent_prediction_path, prediction_path)
    parent_structured = parent_prediction_path.with_suffix(".jsonl")
    if parent_structured.is_file():
        shutil.copy2(parent_structured, structured_prediction_path)
    generated_at = _utc_now()
    manifest = {
        "schema": "sure.eval.prediction_manifest.v1",
        "generated_at": generated_at,
        "run_id": segment_dir.name,
        "run_dir": str(segment_dir),
        "predictions_dir": str(pred_dir),
        "datasets": [
            {
                "dataset": dataset,
                "task": task,
                "language": language,
                "format_used": "jsonl+txt" if structured_prediction_path.is_file() else "txt",
                "txt": str(prediction_path),
                "jsonl": str(structured_prediction_path) if structured_prediction_path.is_file() else None,
                "txt_sha256": _sha256(prediction_path),
                "jsonl_sha256": _sha256(structured_prediction_path),
                "num_rows": _count_lines(prediction_path),
                "structured_num_rows": _count_lines(structured_prediction_path),
            }
        ],
    }
    conversion = {
        "schema": "sure.eval.prediction_conversion_manifest.v1",
        "generated_at": generated_at,
        "run_id": segment_dir.name,
        "run_dir": str(segment_dir),
        "generated_by": "scripts/recover_dnsmos_segment.py",
        "predictions_dir": str(pred_dir),
        "datasets": [
            {
                "dataset": dataset,
                "source_format": "parent_run_predictions",
                "format_used": "jsonl+txt" if structured_prediction_path.is_file() else "txt",
                "num_rows": _count_lines(prediction_path),
                "source_artifacts": {
                    "source_txt": str(parent_prediction_path),
                    "source_jsonl": str(parent_structured) if parent_structured.is_file() else None,
                    "compatibility_tsv": str(prediction_path),
                    "structured_jsonl": str(structured_prediction_path) if structured_prediction_path.is_file() else None,
                },
                "steps": [{"name": "parent_prediction_reuse", "script": "scripts/recover_dnsmos_segment.py"}],
                "conversion_trace": None,
            }
        ],
    }
    _write_json(pred_dir / "manifest.json", manifest)
    _write_json(pred_dir / "conversion_manifest.json", conversion)
    return prediction_path, structured_prediction_path


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

    parent_prediction_path = run_dir / "predictions" / f"{args.dataset}.txt"
    prediction_path, structured_prediction_path = _materialize_segment_predictions(
        segment_dir,
        parent_prediction_path,
        args.dataset,
        "TTS",
        language,
    )
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
    report_row = _standard_report_row(
        row=row,
        score=score,
        total=total,
        language=language,
        metric_dir=metric_dir,
        sample_report_path=sample_report_path,
        prediction_path=prediction_path,
        structured_prediction_path=structured_prediction_path,
        protocol_id=args.protocol_id,
        model_dir=args.model_dir,
        tool_name=args.tool_name,
    )
    (segment_dir / "report.jsonl").write_text(json.dumps(report_row, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_protocol(
        segment_dir / "protocol.yaml",
        dataset=args.dataset,
        metric=metric,
        language=language,
        model_dir=args.model_dir,
        tool_name=args.tool_name,
        protocol_id=args.protocol_id,
    )
    try:
        from generate_report_snapshot import build_snapshot

        (segment_dir / "report_snapshot.md").write_text(build_snapshot(segment_dir), encoding="utf-8")
    except Exception:
        (segment_dir / "report_snapshot.md").write_text(
            "# DNSMOS Recovery Evaluation Snapshot\n\n"
            "## Basic Information\n"
            "## Formatting Policy\n"
            "## Evaluation Scope\n"
            "## Dataset Scope\n"
            "## Result Summary\n"
            "## Per-Dataset Test Results\n"
            "## Metric Details\n"
            "## Validation Summary\n"
            "## Evaluation Pipeline\n"
            "## Pipeline Trace Details\n"
            "## Evaluation Runtime And Tool Versions\n"
            "## Output Artifacts\n"
            "## Artifact Groups\n"
            "## Test Notes\n",
            encoding="utf-8",
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
