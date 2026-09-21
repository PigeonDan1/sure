#!/usr/bin/env python3
"""Gate script for the run_agent unit: validate execution_result.json.

Read-only. Called by the Sure hook with:
    python3 scripts/check_agent_execution.py --run-dir <runDir> --produces <abs>

A terminal failure is a valid outcome of this gate: the run report then has to
say so. What the gate refuses is an inconsistent record — a job status outside
the contract, a product directory that differs from the resolved plan, or a
"succeeded" run whose bundle does not back the claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _nonempty_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def gate_errors(run_dir: Path, result_path: Path) -> list[str]:
    result = _read_json(result_path)
    if result is None:
        return [f"execution_result.json not found or invalid: {result_path}"]
    if result.get("schema") != "sure.agent_eval.execution_result.v1":
        return [f"execution_result.json schema must be sure.agent_eval.execution_result.v1, got {result.get('schema')!r}"]
    artifacts = run_dir / "artifacts"
    spec = _read_json(artifacts / "agent_spec_resolved.json")
    if spec is None:
        return ["agent_spec_resolved.json not found or invalid; run scripts/resolve_agent.py first"]

    errors: list[str] = []
    job_status = result.get("job_status")
    if job_status not in ("succeeded", "failed"):
        errors.append(f"job_status must be succeeded|failed, got {job_status!r}")
    result_agent = result.get("agent") if isinstance(result.get("agent"), dict) else {}
    if str(result_agent.get("name") or "") != str(spec["agent"]["name"]):
        errors.append(
            f"execution_result agent {result_agent.get('name')!r} does not match the resolved spec agent "
            f"{spec['agent']['name']!r}"
        )
    product_dir = Path(str(result.get("product_dir") or ""))
    if str(product_dir) != str(spec["runtime"]["product_dir"]):
        errors.append(
            f"product_dir {product_dir} differs from the resolved plan {spec['runtime']['product_dir']}"
        )

    if job_status == "failed":
        if not str(result.get("error") or "").strip():
            errors.append("a failed execution_result.json must record error")
        if not str(result.get("failed_stage") or "").strip():
            errors.append("a failed execution_result.json must record failed_stage")
        return errors

    rows = result.get("datasets")
    if not isinstance(rows, list) or not rows:
        errors.append("a succeeded execution_result.json must list its datasets")
        return errors
    expected_datasets = [str(item["dataset"]) for item in spec["datasets"]]
    seen = [str(row.get("dataset") or "") for row in rows if isinstance(row, dict)]
    if sorted(seen) != sorted(expected_datasets):
        errors.append(f"execution_result datasets {sorted(seen)} do not match the plan {sorted(expected_datasets)}")
    manifest = _read_json(product_dir / "predictions" / "manifest.json") or {}
    manifest_rows = manifest.get("datasets") if isinstance(manifest.get("datasets"), dict) else {}
    status = _read_json(product_dir / "prediction_generation_status.json") or {}
    status_rows = {
        str(item.get("dataset") or ""): str(item.get("status") or "")
        for item in status.get("datasets") or []
        if isinstance(item, dict)
    }
    for row in rows:
        if not isinstance(row, dict):
            errors.append("dataset rows must be objects")
            continue
        dataset = str(row.get("dataset") or "")
        expected = int(row.get("expected") or 0)
        generated = int(row.get("generated") or 0)
        prediction_file = product_dir / "predictions" / f"{dataset}.txt"
        non_empty = _nonempty_prediction_rows(prediction_file)
        if generated != expected:
            errors.append(f"{dataset}: generated {generated} != expected {expected}")
        if non_empty != expected:
            errors.append(f"{dataset}: predictions/{dataset}.txt carries {non_empty} non-empty rows, expected {expected}")
        recorded = manifest_rows.get(dataset) if isinstance(manifest_rows.get(dataset), dict) else {}
        if prediction_file.is_file() and str(recorded.get("sha256") or "") != _sha256(prediction_file):
            errors.append(
                f"{dataset}: predictions/manifest.json sha256 {recorded.get('sha256')!r} does not match "
                f"predictions/{dataset}.txt"
            )
        if status_rows.get(dataset) != "completed":
            errors.append(f"{dataset}: prediction_generation_status.json status is {status_rows.get(dataset)!r}")
        reference = product_dir / "references" / "sure_benchmark" / "jsonl" / f"{dataset}.jsonl"
        if not reference.is_file():
            errors.append(f"{dataset}: missing reference projection {reference}")
        elif _nonempty_lines(reference) < expected:
            errors.append(f"{dataset}: reference projection carries fewer than {expected} rows")
    if not (product_dir / "protocol.yaml").is_file():
        errors.append(f"protocol.yaml missing from product directory {product_dir}")
    if not (product_dir / "predictions" / "manifest.json").is_file():
        errors.append(f"predictions/manifest.json missing from product directory {product_dir}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate execution_result.json for /sure_agent_eval")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to execution_result.json")
    args = parser.parse_args()
    errors = gate_errors(Path(args.run_dir), Path(args.produces))
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
