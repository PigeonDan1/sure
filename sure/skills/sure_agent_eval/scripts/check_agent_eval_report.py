#!/usr/bin/env python3
"""Gate script for the evaluate unit: validate eval_run_report.json.

Read-only. Called by the Sure hook with:
    python3 scripts/check_agent_eval_report.py --run-dir <runDir> --produces <abs>

The gate proves the report's agent identity against the resolved spec, that a
successful report scores every selected dataset with at least one numeric
metric, and that the batch directory it names exists inside the bundle's
evaluation_runs/ and carries the scoring evidence the report's numbers come
from. A failed report is a valid outcome when it carries an error_code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def gate_errors(run_dir: Path, report_path: Path) -> list[str]:
    report = _read_json(report_path)
    if report is None:
        return [f"eval_run_report.json not found or invalid: {report_path}"]
    if report.get("schema") != "sure.agent_eval.eval_run_report.v1":
        return [f"eval_run_report.json schema must be sure.agent_eval.eval_run_report.v1, got {report.get('schema')!r}"]
    artifacts = run_dir / "artifacts"
    spec = _read_json(artifacts / "agent_spec_resolved.json")
    if spec is None:
        return ["agent_spec_resolved.json not found or invalid; run scripts/resolve_agent.py first"]

    errors: list[str] = []
    if report.get("evaluation_only") is not True:
        errors.append("evaluation_only must be true: the evaluate unit scores existing predictions")
    agent = report.get("agent") if isinstance(report.get("agent"), dict) else {}
    if str(agent.get("name") or "") != str(spec["agent"]["name"]):
        errors.append(f"report agent {agent.get('name')!r} does not match the resolved spec {spec['agent']['name']!r}")
    if str(agent.get("spec_sha256") or "") != str(spec["agent"]["spec_sha256"]):
        errors.append("report agent.spec_sha256 does not match the resolved spec")

    status = report.get("status")
    if status not in ("success", "failed"):
        errors.append(f"status must be success|failed, got {status!r}")
        return errors
    if status == "failed":
        if not str(report.get("error_code") or "").strip():
            errors.append("a failed eval_run_report must declare error_code")
        return errors

    batch_id = str(report.get("batch_id") or "")
    batch_dir = Path(str(report.get("evaluation_runs_dir") or ""))
    product_dir = Path(str(report.get("product_dir") or ""))
    if not batch_id:
        errors.append("a successful eval_run_report must record batch_id")
    if str(product_dir) != str(spec["runtime"]["product_dir"]):
        errors.append(f"product_dir {product_dir} differs from the resolved plan {spec['runtime']['product_dir']}")
    if batch_dir != product_dir / "evaluation_runs" / batch_id:
        errors.append(
            f"evaluation_runs_dir must be <product_dir>/evaluation_runs/{batch_id}, got {batch_dir}"
        )
    if not batch_dir.is_dir():
        errors.append(f"batch directory does not exist: {batch_dir}")

    validation = _read_json(batch_dir / "validation_payload.json")
    if validation is None:
        errors.append(f"validation_payload.json not found or invalid: {batch_dir / 'validation_payload.json'}")
    elif validation.get("is_valid") is not True:
        errors.append("validation_payload.is_valid must be true")
    evaluation = _read_json(batch_dir / "evaluation_payload.json")
    if evaluation is None:
        errors.append(f"evaluation_payload.json not found or invalid: {batch_dir / 'evaluation_payload.json'}")
    payload_scores = {
        (str(row.get("dataset") or ""), str(row.get("metric") or ""), str(row.get("pipeline_id") or "")): row["result"].get("score")
        for row in (evaluation or {}).get("results") or []
        if isinstance(row, dict) and isinstance(row.get("result"), dict)
    }

    rows = report.get("datasets")
    if not isinstance(rows, list):
        return errors + ["a successful eval_run_report must list its datasets"]
    by_dataset = {str(row.get("dataset") or ""): row for row in rows if isinstance(row, dict)}
    for item in spec["datasets"]:
        dataset = str(item["dataset"])
        row = by_dataset.get(dataset)
        if row is None:
            errors.append(f"{dataset}: missing from the eval report")
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            errors.append(f"{dataset}: a successful eval report must carry at least one metric score")
            continue
        scored = [
            metric
            for metric in metrics
            if isinstance(metric, dict) and isinstance(metric.get("score"), (int, float))
        ]
        if not scored:
            errors.append(f"{dataset}: no numeric metric score recorded")
        if evaluation is None:
            continue
        for metric in scored:
            name = str(metric.get("metric") or "")
            key = (dataset, name, str(metric.get("pipeline_id") or ""))
            if key not in payload_scores:
                errors.append(f"{dataset}: metric {name!r} via {key[2]!r} is not scored by the evaluation payload")
            elif payload_scores[key] != metric["score"]:
                errors.append(
                    f"{dataset}: metric {name!r} score {metric['score']!r} differs from the evaluation payload "
                    f"{payload_scores[key]!r}"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate eval_run_report.json for /sure_agent_eval")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to eval_run_report.json")
    args = parser.parse_args()
    errors = gate_errors(Path(args.run_dir), Path(args.produces))
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
