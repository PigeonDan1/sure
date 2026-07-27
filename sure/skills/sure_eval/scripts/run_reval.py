#!/usr/bin/env python3
"""Run /sure_reval: reuse existing predictions and recompute metrics."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from import_prediction_source import import_predictions
from resolve_evaluation_engine import resolve_engine_root
from resolve_prediction_source import build_payload as resolve_prediction_source


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in str(value)).strip("_") or "unknown"


def _split_values(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for item in str(value).replace(",", " ").split():
            item = item.strip()
            if item and item not in out:
                out.append(item)
    return out


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run(command: list[str], *, cwd: Path = SCRIPT_DIR, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "command failed "
            f"(exit={completed.returncode}): {' '.join(command)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def _harness_config(run_dir: Path, explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise FileNotFoundError(path)
        return path.resolve()
    env_config = os.environ.get("SURE_EVAL_CONFIG")
    if env_config:
        path = Path(env_config).expanduser()
        if not path.exists():
            raise FileNotFoundError(path)
        return path.resolve()
    base_config = HARNESS_ROOT / "sure" / "external" / "sure-evaluation" / "config" / "default.yaml"
    datasets_root = Path(os.environ.get("SURE_EVAL_DATASETS_ROOT", HARNESS_ROOT / "data" / "datasets"))
    if not base_config.is_file():
        raise FileNotFoundError(f"sure-evaluation default config not found: {base_config}")
    if not (datasets_root / "sure_benchmark" / "jsonl").is_dir():
        raise FileNotFoundError(f"dataset root must contain sure_benchmark/jsonl: {datasets_root}")
    config = yaml.safe_load(base_config.read_text(encoding="utf-8")) or {}
    data = dict(config.get("data") or {})
    data.update(
        {
            "root": str(HARNESS_ROOT / "data"),
            "cache": str(HARNESS_ROOT / "data" / "cache"),
            "models": str(HARNESS_ROOT / "data" / "models"),
            "datasets": str(datasets_root),
            "results": str(run_dir / "_unused_results"),
        }
    )
    config["data"] = data
    output = run_dir / "_harness_config.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return output.resolve()


def _engine_info(engine_root: str | None) -> dict[str, Any]:
    resolved = resolve_engine_root(engine_root)
    if resolved is None:
        return {"source": None, "engine_root": None, "commit": None}
    source, root = resolved
    completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    return {
        "source": source,
        "engine_root": str(root),
        "commit": completed.stdout.strip() if completed.returncode == 0 else None,
    }


def _route_plan_from_payload(payload: dict[str, Any], run_dir: Path, engine: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    return {
        "schema": "sure.reval.route_plan.v1",
        "generated_at": _utc_now(),
        "engine": engine,
        "can_run_now": True,
        "selected_routes": [
            {
                "dataset": row.get("dataset"),
                "task": row.get("task"),
                "language": row.get("language"),
                "metric": row.get("metric"),
                "pipeline_id": row.get("pipeline_id"),
                "route_id": row.get("route_id"),
                "nodes": row.get("nodes") or (row.get("pipeline") or {}).get("nodes"),
            }
            for row in rows
            if isinstance(row, dict)
        ],
        "plan_path": str(run_dir / "evaluation_route_plan.json"),
    }


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        score = result.get("score")
        key = (str(row.get("dataset") or ""), str(row.get("metric") or ""))
        grouped.setdefault(key, []).append(
            {
                "pipeline_id": row.get("pipeline_id"),
                "nodes": row.get("nodes") or (row.get("pipeline") or {}).get("nodes"),
                "score": score,
            }
        )
    comparisons = []
    for (dataset, metric), items in grouped.items():
        scores = [item.get("score") for item in items if isinstance(item.get("score"), (int, float))]
        comparison: dict[str, Any] = {"dataset": dataset, "metric": metric, "pipelines": items}
        if scores:
            comparison["min_score"] = min(scores)
            comparison["max_score"] = max(scores)
            comparison["score_spread"] = max(scores) - min(scores)
        comparisons.append(comparison)
    return {"num_results": len(rows), "comparisons": comparisons}


def run_reval(args: argparse.Namespace) -> dict[str, Any]:
    source_payload = resolve_prediction_source(
        argparse.Namespace(
            source=args.source,
            model=args.model,
            model_dir=args.model_dir,
            datasets=args.datasets,
            protocol_id=args.protocol_id,
            output=None,
        )
    )
    model_name = args.model or str(source_payload.get("model_name") or "unknown_model")
    run_id = args.run_id or f"sure_reval_{_safe(model_name)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tmp_root = Path(args.tmp_root).expanduser() if args.tmp_root else Path.home() / "tmp" / "sure_reval"
    run_dir = Path(args.output_dir).expanduser() if args.output_dir else tmp_root / _safe(model_name) / run_id
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    config_path = _harness_config(run_dir, args.config)
    source_path = run_dir / "prediction_source_resolved.json"
    _write_json(source_path, source_payload)

    reuse_manifest = import_predictions(
        argparse.Namespace(
            source_resolved=str(source_path),
            run_dir=str(run_dir),
            copy_mode=args.copy_mode,
            max_samples=args.max_samples,
            config=str(config_path),
            output=str(run_dir / "prediction_reuse_manifest.json"),
        )
    )

    datasets = [str(item) for item in source_payload.get("datasets") or []]
    validate_cmd = [
        sys.executable,
        str(SCRIPT_DIR / "validate_prediction_files.py"),
        "--dataset",
        *datasets,
        "--pred-dir",
        str(run_dir / "predictions"),
        "--require-nonempty",
        "--config",
        str(config_path),
        "--output",
        str(run_dir / "validation_payload.json"),
    ]
    if args.max_samples > 0:
        validate_cmd.extend(["--max-samples", str(args.max_samples)])
    _run(validate_cmd)

    metrics = _split_values(args.metrics)
    pipeline_ids = _split_values(args.pipeline_id)
    eval_cmd = [
        sys.executable,
        str(SCRIPT_DIR / "evaluate_predictions.py"),
        "--dataset",
        *datasets,
        "--pred-dir",
        str(run_dir / "predictions"),
        "--tool-name",
        model_name,
        "--protocol-id",
        str(source_payload.get("protocol_id") or args.protocol_id or "strict_core"),
        "--run-dir",
        str(run_dir),
        "--validation-payload",
        str(run_dir / "validation_payload.json"),
        "--config",
        str(config_path),
        "--evaluation-backend",
        "external",
        "--strict-main-flow",
        "--output",
        str(run_dir / "evaluation_payload.json"),
        "--external-runs-dir",
        str(run_dir / "evaluation_runs"),
        "--evaluation-device",
        args.device,
        "--no-copy-source-report",
    ]
    model_dir = args.model_dir or source_payload.get("model_dir")
    if model_dir:
        eval_cmd.extend(["--model-dir", str(model_dir)])
    if args.evaluation_engine_root:
        eval_cmd.extend(["--evaluation-engine-root", args.evaluation_engine_root])
    for metric in metrics:
        eval_cmd.extend(["--metric", metric])
    for pipeline_id in pipeline_ids:
        eval_cmd.extend(["--pipeline-id", pipeline_id])
    _run(eval_cmd)

    _run(
        [
            sys.executable,
            str(SCRIPT_DIR / "generate_report_snapshot.py"),
            "--run-dir",
            str(run_dir),
            "--output",
            str(run_dir / "report_snapshot.md"),
        ]
    )

    payload = _read_json(run_dir / "evaluation_payload.json")
    engine = _engine_info(args.evaluation_engine_root)
    route_plan = _route_plan_from_payload(payload, run_dir, engine)
    _write_json(run_dir / "evaluation_route_plan.json", route_plan)
    summary = _summary(payload)

    manifest = {
        "schema": "sure.reval.model_eval_manifest.v1",
        "run_id": run_id,
        "model_name": model_name,
        "model_dir": str(model_dir or ""),
        "created_at": _utc_now(),
        "status": "success",
        "selected_datasets": datasets,
        "evaluation_only": True,
        "old_evaluation_reused": False,
        "source_prediction": {
            "source": source_payload.get("source"),
            "source_kind": source_payload.get("source_kind"),
            "source_run_dir": source_payload.get("source_run_dir"),
            "source_results_dir": source_payload.get("source_results_dir"),
            "source_run_id": source_payload.get("source_run_id"),
        },
        "artifacts": {
            "prediction_source_resolved": str(source_path),
            "prediction_reuse_manifest": str(run_dir / "prediction_reuse_manifest.json"),
            "evaluation_route_plan": str(run_dir / "evaluation_route_plan.json"),
            "validation_payload": str(run_dir / "validation_payload.json"),
            "evaluation_payload": str(run_dir / "evaluation_payload.json"),
            "protocol": str(run_dir / "protocol.yaml"),
            "report_jsonl": str(run_dir / "report.jsonl"),
            "report_snapshot": str(run_dir / "report_snapshot.md"),
            "predictions_dir": str(run_dir / "predictions"),
            "metrics_dir": str(run_dir / "metrics"),
            "sample_reports_dir": str(run_dir / "sample_reports"),
        },
        "summary": summary,
        "prediction_reuse": reuse_manifest,
    }
    _write_json(run_dir / "model_eval_manifest.json", manifest)

    dataset_total = sum(int(item.get("dataset_total_samples") or 0) for item in reuse_manifest.get("imported", []))
    evaluated_samples = sum(int(item.get("imported_samples") or 0) for item in reuse_manifest.get("imported", []))
    run_report = {
        "run_id": run_id,
        "timestamp": _utc_now(),
        "task_type": "rejudge_existing_predictions",
        "goal": "Reuse existing predictions and recompute evaluation routes without model inference.",
        "selected_datasets": datasets,
        "executed_steps": [
            "SOURCE_RESOLUTION",
            "PREDICTION_IMPORT",
            "PREDICTION_VALIDATION",
            "ROUTE_BACKED_EVALUATION",
            "REPORT_SNAPSHOT",
        ],
        "status": "success",
        "report_persisted": True,
        "execution_path_actual": "local_bash",
        "execution_path_requested": "local",
        "evaluation_only": True,
        "run_dir": str(run_dir),
        "evaluation_run_dir": str(run_dir),
        "artifact_root": str(run_dir),
        "model_eval_manifest": str(run_dir / "model_eval_manifest.json"),
        "device_request": args.device,
        "device_actual": args.device,
        "max_samples": args.max_samples,
        "dataset_total_samples": dataset_total,
        "evaluated_samples": evaluated_samples,
        "artifacts": manifest["artifacts"],
        "next_action": "Review score differences across selected pipelines.",
        "notes": [
            "No model server was started and no inference was run.",
            "Predictions were copied or filtered into this run before validation.",
            "Old evaluation reports and metric artifacts were not reused.",
        ],
    }
    _write_json(run_dir / "main_agent_run_report.json", run_report)
    _run(
        [
            sys.executable,
            str(SCRIPT_DIR / "check_run_report.py"),
            "--run-dir",
            str(run_dir),
            "--produces",
            str(run_dir / "main_agent_run_report.json"),
        ]
    )

    reval_report = {
        "schema": "sure.reval.run_report.v1",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "model_name": model_name,
        "datasets": datasets,
        "metrics": metrics,
        "pipeline_ids": pipeline_ids,
        "evaluation_only": True,
        "old_evaluation_reused": False,
        "artifacts": manifest["artifacts"] | {
            "model_eval_manifest": str(run_dir / "model_eval_manifest.json"),
            "main_agent_run_report": str(run_dir / "main_agent_run_report.json"),
        },
        "summary": summary,
    }
    _write_json(run_dir / "reval_run_report.json", reval_report)
    print(json.dumps(reval_report, indent=2, ensure_ascii=False))
    return reval_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Reuse existing predictions and rerun SURE evaluation routes")
    parser.add_argument("--source", required=True, help="Existing results dir, run dir, or predictions dir")
    parser.add_argument("--model")
    parser.add_argument("--model-dir")
    parser.add_argument("--datasets", nargs="*")
    parser.add_argument("--metrics", nargs="*")
    parser.add_argument("--pipeline-id", action="append")
    parser.add_argument("--max-samples", type=int, default=0)
    parser.add_argument("--copy-mode", choices=("copy", "hardlink"), default="copy")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--protocol-id", default="strict_core")
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir")
    parser.add_argument("--tmp-root")
    parser.add_argument("--config")
    parser.add_argument("--evaluation-engine-root")
    args = parser.parse_args()
    run_reval(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
