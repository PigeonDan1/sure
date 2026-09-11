#!/usr/bin/env python3
"""Score an agent bundle with the pinned sure-evaluation engine.

Thin wrapper over the /sure_eval backend (``run_eval.py`` helpers +
``evaluate_predictions.py`` with the external backend in the locked Evaluation
Runtime): validates the bundle predictions, resolves each requested metric to
the engine's default pipeline for the dataset's task and language, runs the
scorer, appends the batch below ``<product_dir>/evaluation_runs/`` and writes
``eval_run_report.json`` (schema sure.agent_eval.eval_run_report.v1) into the
run artifacts. Evaluation-only: this script never starts a model.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = Path(__file__).resolve().parents[4]
SURE_INFER_SCRIPTS = SCRIPT_DIR.parents[1] / "sure_infer" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SURE_INFER_SCRIPTS))
sys.path.insert(0, str(HARNESS_ROOT))

import run_eval  # noqa: E402

EVAL_REPORT_SCHEMA = "sure.agent_eval.eval_run_report.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _count_nonempty_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def _failed_report(
    args: argparse.Namespace,
    *,
    spec: dict[str, Any] | None,
    error_code: str,
    batch_id: str | None = None,
    product_dir: str = "",
    evaluation_runs_dir: str = "",
) -> dict[str, Any]:
    agent = {"name": "", "spec_sha256": "0" * 64}
    if spec:
        agent = {
            "name": str(spec["agent"]["name"]),
            "spec_sha256": str(spec["agent"]["spec_sha256"]),
        }
    report = {
        "schema": EVAL_REPORT_SCHEMA,
        "run_id": str(args.run_id),
        "status": "failed",
        "error_code": error_code,
        "agent": agent,
        "evaluation_only": True,
        "batch_id": batch_id or f"agent_eval_{secrets.token_hex(12)}",
        "product_dir": product_dir,
        "evaluation_runs_dir": evaluation_runs_dir,
        "datasets": [],
        "engine": None,
        "created_at": _utc_now(),
    }
    _write_json(Path(args.run_dir) / "artifacts" / "eval_run_report.json", report)
    return report


def run_agent_eval(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).expanduser().resolve()
    artifacts = run_dir / "artifacts"
    spec_path = artifacts / "agent_spec_resolved.json"
    execution_path = artifacts / "execution_result.json"
    if not spec_path.is_file():
        return _failed_report(args, spec=None, error_code="AGENT_SPEC_NOT_RESOLVED")
    spec = _read_json(spec_path)
    agent = spec["agent"]
    if not execution_path.is_file():
        return _failed_report(args, spec=spec, error_code="AGENT_EXECUTION_MISSING")
    execution = _read_json(execution_path)
    product_dir = Path(str(execution.get("product_dir") or spec["runtime"]["product_dir"]))
    if execution.get("job_status") != "succeeded":
        return _failed_report(
            args,
            spec=spec,
            error_code="AGENT_EXECUTION_FAILED",
            product_dir=str(product_dir),
        )

    datasets = [str(item["dataset"]) for item in spec["datasets"]]
    batch_id = f"agent_eval_{secrets.token_hex(12)}"
    batch_dir = product_dir / "evaluation_runs" / batch_id
    scratch = run_dir / "scratch" / "evaluate"
    scratch.mkdir(parents=True, exist_ok=True)
    batch_dir.mkdir(parents=True, exist_ok=True)

    try:
        source_payload = {
            "source_kind": "local_infer_run",
            "source_results_dir": str(product_dir),
            "model_dir": spec["stages"][0]["model_dir"],
        }
        config_path = run_eval._harness_config(
            scratch,
            None,
            source=source_payload,
            approved_models_root=Path(spec["stages"][0]["model_dir"]).parent,
            approved_results_root=None,
        )
        validate_cmd = [
            sys.executable,
            str(SURE_INFER_SCRIPTS / "validate_prediction_files.py"),
            "--dataset",
            *datasets,
            "--pred-dir",
            str(product_dir / "predictions"),
            "--require-nonempty",
            "--config",
            str(config_path),
            "--output",
            str(scratch / "validation_payload.json"),
        ]
        run_eval._run(validate_cmd)

        # evaluate_predictions.py records run-relative artifact paths, so the
        # predictions it scores must live under its --run-dir; stage a copy in
        # scratch instead of pointing it at the product tree.
        scratch_pred = scratch / "predictions"
        if scratch_pred.exists():
            shutil.rmtree(scratch_pred)
        shutil.copytree(product_dir / "predictions", scratch_pred)

        imported = [
            {"dataset": str(item["dataset"]), "task": str(item["task"]), "language": str(item["language"])}
            for item in spec["datasets"]
        ]
        engine_root = run_eval.EVALUATION_ENGINE_ROOT
        pipeline_ids = run_eval._pipeline_ids_for_metrics(
            list(spec["metrics"]), engine_root=engine_root, imported=imported
        )

        eval_cmd = [
            sys.executable,
            str(SURE_INFER_SCRIPTS / "evaluate_predictions.py"),
            "--dataset",
            *datasets,
            "--pred-dir",
            str(scratch_pred),
            "--tool-name",
            str(agent["name"]),
            "--protocol-id",
            "standard_system",
            "--run-dir",
            str(scratch),
            "--validation-payload",
            str(scratch / "validation_payload.json"),
            "--config",
            str(config_path),
            "--evaluation-backend",
            "external",
            "--output",
            str(scratch / "evaluation_payload.json"),
            "--external-runs-dir",
            str(batch_dir),
            "--evaluation-device",
            str(args.device),
            "--no-copy-source-report",
            "--evaluation-engine-root",
            str(engine_root),
        ]
        for pipeline_id in pipeline_ids:
            eval_cmd.extend(["--pipeline-id", pipeline_id])
        eval_env = os.environ.copy()
        eval_env.update(
            {
                "SURE_EVAL_EXECUTION_PATH": "agent_chain",
                "SURE_EVAL_EXECUTION_REQUESTED": "local",
                "SURE_EVAL_EXECUTION_SURFACE_TYPE": "sure_agent_eval",
                "SURE_EVAL_EXECUTION_GENERATION_METHOD": "agent_runner_chain",
                "SURE_EVAL_PREDICTION_GENERATED_BY": "scripts/agent_runner.py",
            }
        )
        run_eval._run(eval_cmd, env=eval_env)

        payload = _read_json(scratch / "evaluation_payload.json")
        engine = None
        try:
            info = run_eval._engine_info(str(engine_root))
            engine = {"engine_root": info["engine_root"], "commit": info["commit"]}
        except Exception:
            engine = None

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in payload.get("results") or []:
            if not isinstance(row, dict):
                continue
            dataset = str(row.get("dataset") or "")
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            score = result.get("score")
            grouped.setdefault(dataset, []).append(
                {
                    "metric": str(row.get("metric") or ""),
                    "pipeline_id": str(row.get("pipeline_id") or ""),
                    "score": score if isinstance(score, (int, float)) else None,
                }
            )
        by_dataset = {str(item["dataset"]): item for item in spec["datasets"]}
        report_datasets = []
        for dataset in datasets:
            meta = by_dataset[dataset]
            report_datasets.append(
                {
                    "dataset": dataset,
                    "task": str(meta["task"]),
                    "language": str(meta["language"]),
                    "num_samples": _count_nonempty_lines(product_dir / "predictions" / f"{dataset}.txt"),
                    "metrics": grouped.get(dataset, []),
                }
            )
        # Persist the scoring evidence next to the engine's per-dataset outputs.
        for artifact_name in ("validation_payload.json", "evaluation_payload.json"):
            shutil.copy2(scratch / artifact_name, batch_dir / artifact_name)

        report = {
            "schema": EVAL_REPORT_SCHEMA,
            "run_id": str(args.run_id),
            "status": "success",
            "error_code": None,
            "agent": {"name": str(agent["name"]), "spec_sha256": str(agent["spec_sha256"])},
            "evaluation_only": True,
            "batch_id": batch_id,
            "product_dir": str(product_dir),
            "evaluation_runs_dir": str(batch_dir),
            "datasets": report_datasets,
            "engine": engine,
            "created_at": _utc_now(),
        }
        _write_json(artifacts / "eval_run_report.json", report)
        return report
    except Exception as exc:  # noqa: BLE001 - every failure becomes a terminal failed report
        print(str(exc), file=sys.stderr)
        return _failed_report(
            args,
            spec=spec,
            error_code="EVALUATION_FAILED",
            batch_id=batch_id,
            product_dir=str(product_dir),
            evaluation_runs_dir=str(batch_dir),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Score an agent bundle with the pinned evaluation engine")
    parser.add_argument("--run-dir", required=True, help="Sure invocation run directory")
    parser.add_argument("--run-id", help="Run id recorded in the report (default: run directory name)")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    args.run_id = args.run_id or Path(args.run_dir).expanduser().resolve().name
    report = run_agent_eval(args)
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
