#!/usr/bin/env python3
"""Gate script for the execute_inference unit: validate execution_result.json.

Read-only. Called by the Sure hook with:
    python3 scripts/check_execution_result.py --run-dir <runDir> --produces <abs>

A terminal failure is a valid outcome of this gate: the run report then has to
say so. What the gate refuses is an inconsistent record — a job still running,
a path that differs from the plan, a surface whose deployment binding drifted
from the approved input, or a "succeeded" run whose product tree does not back
the claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import check_execution_surface_compliance as compliance
from dataset_identity import projection_stems
from execution_result_checks import validation_errors


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _nonempty_prediction_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t", 1)
        if len(parts) > 1 and parts[1].strip():
            count += 1
    return count


def gate_errors(run_dir: Path, result_path: Path) -> list[str]:
    result = _read_json(result_path)
    if result is None:
        return [f"execution_result.json not found or invalid: {result_path}"]
    artifacts = run_dir / "artifacts"
    surface = _read_json(artifacts / "execution_surface.json")
    if surface is None:
        return ["execution_surface.json not found or invalid; run scripts/run_infer.py first"]
    eval_input = _read_json(artifacts / "eval_input_resolved.json")
    if eval_input is None:
        return ["eval_input_resolved.json not found or invalid"]

    execution = surface.get("execution") if isinstance(surface.get("execution"), dict) else {}
    errors = validation_errors(result, str(execution.get("path_planned") or ""))
    workers = int((eval_input.get("runtime") or {}).get("num_workers", 1))
    parallel = result.get("parallelism") if isinstance(result.get("parallelism"), dict) else {}
    if workers > 1 and parallel.get("requested_workers") != workers:
        errors.append("execution_result.json parallelism differs from requested num_workers")
    selection = (eval_input.get("runtime") or {}).get("worker_selection")
    if selection and (parallel.get("worker_selection") != selection or parallel.get("requested_workers") != workers):
        errors.append("execution_result.json Worker selection differs from the resolved plan")

    model = eval_input.get("model") if isinstance(eval_input.get("model"), dict) else {}
    approved = model.get("deployment_binding")
    if not isinstance(approved, dict):
        errors.append("eval_input_resolved.json carries no approved deployment binding")
    else:
        errors.extend(compliance.binding_mismatches(surface.get("deployment_binding"), compliance.expected_binding_summary(approved)))

    if result.get("job_status") != "succeeded":
        return errors

    product_dir = Path(str(result.get("product_dir") or ""))
    if not product_dir.is_dir():
        errors.append(f"product_dir does not exist: {product_dir}")
        return errors
    rows = result.get("datasets")
    if not isinstance(rows, list) or not rows:
        errors.append("a succeeded execution_result.json must list its datasets")
        return errors
    status = _read_json(product_dir / "prediction_generation_status.json") or {}
    status_rows = {
        str(row.get("dataset")): row
        for row in status.get("datasets", [])
        if isinstance(row, dict) and row.get("dataset")
    }
    validation = _read_json(product_dir / "validation_payload.json")
    if not validation:
        errors.append(f"validation_payload.json is missing or invalid under {product_dir}")
    elif validation.get("is_valid") is not True:
        errors.append("validation_payload.json must declare is_valid=true for a succeeded execution")
    validation_rows = {
        str(row.get("dataset")): row
        for row in validation.get("results", [])
        if isinstance(row, dict) and row.get("dataset")
    } if isinstance(validation, dict) else {}
    # The product tree names files by the projection stem; the execution_result
    # rows report the canonical id. eval_input_resolved.json carries the
    # authoritative per-run mapping between the two.
    stems = projection_stems(eval_input.get("datasets"))
    plans = {stems[str(row["name"])]: row for row in eval_input.get("datasets", [])
             if isinstance(row, dict) and "planned_workers" in row}
    if not (product_dir / "protocol.yaml").is_file():
        errors.append(f"protocol.yaml is missing under {product_dir}")
    parallel_rows = {str(row.get("dataset")): row for row in parallel.get("datasets", []) if isinstance(row, dict)}
    if workers > 1 and len(set(parallel.get("gpu_devices", []))) != workers:
        errors.append("succeeded parallel execution must record one distinct GPU per requested Worker")
    for row in rows:
        if not isinstance(row, dict) or not row.get("dataset"):
            errors.append("datasets[] entries must name their dataset")
            continue
        name = str(row["dataset"])
        stem = stems.get(name, name)
        actual = _nonempty_prediction_rows(product_dir / "predictions" / f"{stem}.txt")
        if actual != row.get("generated"):
            errors.append(
                f"predictions/{stem}.txt has {actual} non-empty rows but execution_result.json claims {row.get('generated')!r}"
            )
        dataset_status = status_rows.get(stem)
        plan = plans.get(stem)
        if plan:
            audit = {"planned_workers": plan["planned_workers"], "total_samples": plan["num_samples"],
                     "selected_samples": plan["selected_samples"]}
            summary = parallel_rows.get(stem, {})
            for record in (dataset_status or {}, summary):
                if any(record.get(key) != value for key, value in audit.items()):
                    errors.append(f"Worker selection evidence differs from the plan for {name}")
                effective = record.get("effective_workers")
                if type(effective) is not int or not 0 <= effective <= plan["planned_workers"]:
                    errors.append(f"Invalid effective Worker count for {name}")
            if row.get("expected") != plan["selected_samples"]:
                errors.append(f"Execution sample scope differs from the Worker plan for {name}")
        if dataset_status is None:
            errors.append(f"prediction_generation_status.json has no entry for {stem}")
        elif dataset_status.get("status") != "completed":
            errors.append(f"prediction_generation_status.json marks {stem} as {dataset_status.get('status')!r}, not completed")
        validation_row = validation_rows.get(stem)
        if validation_row is None:
            errors.append(f"validation_payload.json has no result for {stem}")
        elif validation_row.get("is_valid") is not True:
            errors.append(f"validation_payload.json marks {stem} as invalid")
        if workers > 1:
            summary = parallel_rows.get(stem, {})
            report_path = product_dir / "parallel_inference" / stem / "merge_report.json"
            report = _read_json(report_path) or {}
            if plan and (report.get("requested_workers") != plan["planned_workers"]
                         or any(report.get(key) != value for key, value in audit.items())):
                errors.append(f"Parallel merge Worker plan differs for {name}")
            if (summary.get("status") != "completed" or report.get("status") != "completed"
                    or report.get("missing_keys") or report.get("generated") != row.get("generated")
                    or report.get("expected") != row.get("expected")):
                errors.append(f"Parallel merge evidence is incomplete or disagrees for {name}")
            for suffix, digest_key in ((".txt", "txt_sha256"), (".jsonl", "jsonl_sha256")):
                prediction = product_dir / "predictions" / f"{stem}{suffix}"
                if prediction.is_file() and hashlib.sha256(prediction.read_bytes()).hexdigest() != report.get(digest_key):
                    errors.append(f"Parallel prediction digest changed after merge: {prediction.name}")
        if not (product_dir / "references" / "sure_benchmark" / "jsonl" / f"{stem}.jsonl").is_file():
            errors.append(f"references/sure_benchmark/jsonl/{stem}.jsonl is missing under {product_dir}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to execution_result.json")
    args = parser.parse_args()
    errors = gate_errors(Path(args.run_dir), Path(args.produces))
    if errors:
        print("execute_inference gate failed:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 1
    print("execute_inference OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
