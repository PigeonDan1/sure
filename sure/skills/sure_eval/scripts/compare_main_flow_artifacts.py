#!/usr/bin/env python3
"""Compare two SURE-EVAL main-flow artifact directories."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


REQUIRED_ROOT_FILES = ("evaluation_payload.json", "report.jsonl", "protocol.yaml", "report_snapshot.md")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml

        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _metric_slug(metric: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in metric.lower()) or "metric"


def _prediction_keys(path: Path, limit: int | None) -> list[str]:
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        key = line.split("\t", 1)[0].split(None, 1)[0]
        keys.append(key)
        if limit is not None and len(keys) >= limit:
            break
    return keys


def _prediction_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            key, value = line.split("\t", 1)
        else:
            parts = line.split(None, 1)
            key = parts[0]
            value = parts[1] if len(parts) > 1 else ""
        values[key] = value
    return values


def _structured_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and row.get("key"):
            values[str(row["key"])] = str(row.get("normalized_prediction") or "")
    return values


def _row_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("dataset") or ""), str(row.get("metric") or "")


def _score(row: dict[str, Any]) -> float | None:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    value = result.get("score")
    if value is None and result.get("score_key"):
        value = result.get(result["score_key"])
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _task(row: dict[str, Any]) -> str:
    return str(row.get("task") or "").upper()


def _node_ids(row: dict[str, Any]) -> list[str]:
    candidates: Any = row.get("nodes")
    if not candidates:
        pipeline = row.get("pipeline") if isinstance(row.get("pipeline"), dict) else {}
        candidates = pipeline.get("nodes")
    if not candidates:
        context = row.get("evaluation_context") if isinstance(row.get("evaluation_context"), dict) else {}
        candidates = context.get("nodes")
    node_ids: list[str] = []
    if isinstance(candidates, list):
        for node in candidates:
            if isinstance(node, dict):
                value = node.get("node_id")
            else:
                value = node
            if value:
                node_ids.append(str(value))
    return node_ids


def _allows_generated_audio_score_drift(row: dict[str, Any]) -> bool:
    return _task(row) in {"TTS", "VC"}


def _validate_tree(root: Path, label: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    for name in REQUIRED_ROOT_FILES:
        if not (root / name).is_file():
            errors.append(f"{label}: missing {root / name}")

    payload: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    if (root / "evaluation_payload.json").is_file():
        try:
            payload = _read_json(root / "evaluation_payload.json")
        except Exception as exc:
            errors.append(f"{label}: invalid evaluation_payload.json: {exc}")
        else:
            if payload.get("schema") != "sure.eval.payload.v2":
                errors.append(f"{label}: evaluation_payload.json schema must be sure.eval.payload.v2")
    if (root / "protocol.yaml").is_file():
        protocol = _read_yaml(root / "protocol.yaml")
        if protocol.get("schema") != "sure.eval.inference_protocol.v1":
            errors.append(f"{label}: protocol.yaml schema must be sure.eval.inference_protocol.v1")
    if (root / "report.jsonl").is_file():
        try:
            rows = _read_jsonl(root / "report.jsonl")
        except Exception as exc:
            errors.append(f"{label}: invalid report.jsonl: {exc}")
        else:
            for index, row in enumerate(rows, 1):
                if row.get("schema") != "sure.eval.report.dataset_metric.v1":
                    errors.append(f"{label}: report.jsonl line {index} schema must be sure.eval.report.dataset_metric.v1")

    payload_rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    if not payload_rows:
        errors.append(f"{label}: evaluation_payload.json has no results")
    if payload_rows and rows and len(payload_rows) != len(rows):
        errors.append(f"{label}: payload result count {len(payload_rows)} != report rows {len(rows)}")

    for row in payload_rows:
        if not isinstance(row, dict):
            errors.append(f"{label}: payload result row is not an object")
            continue
        dataset, metric = _row_key(row)
        if not dataset or not metric:
            errors.append(f"{label}: result row missing dataset or metric")
            continue
        prediction_txt = root / "predictions" / f"{dataset}.txt"
        prediction_jsonl = root / "predictions" / f"{dataset}.jsonl"
        if not prediction_txt.is_file():
            errors.append(f"{label}: missing {prediction_txt}")
        if not prediction_jsonl.is_file():
            errors.append(f"{label}: missing {prediction_jsonl}")
        if prediction_txt.is_file() and prediction_jsonl.is_file():
            try:
                txt_values = _prediction_values(prediction_txt)
                structured_values = _structured_values(prediction_jsonl)
            except Exception as exc:
                errors.append(f"{label}: failed to inspect prediction projection for {dataset}: {exc}")
            else:
                if set(txt_values) != set(structured_values):
                    errors.append(f"{label}: prediction txt/jsonl keys differ for {dataset}")
                elif any(txt_values[key] != structured_values[key] for key in txt_values):
                    errors.append(f"{label}: prediction txt/jsonl normalized values differ for {dataset}")
        metric_dir = root / "metrics" / dataset / _metric_slug(metric)
        if not (metric_dir / "report.json").is_file():
            errors.append(f"{label}: missing {metric_dir / 'report.json'}")
        if not (metric_dir / "pipeline_description.json").is_file():
            errors.append(f"{label}: missing {metric_dir / 'pipeline_description.json'}")
        sample_report = root / "sample_reports" / dataset / f"{_metric_slug(metric)}.jsonl"
        if not sample_report.is_file():
            errors.append(f"{label}: missing {sample_report}")
    for name in ("manifest.json", "conversion_manifest.json"):
        path = root / "predictions" / name
        if not path.is_file():
            errors.append(f"{label}: missing {path}")
    return payload, errors


def compare_runs(reference: Path, candidate: Path, *, sample_limit: int, score_tolerance: float) -> dict[str, Any]:
    reference_payload, reference_errors = _validate_tree(reference, "reference")
    candidate_payload, candidate_errors = _validate_tree(candidate, "candidate")
    errors = reference_errors + candidate_errors
    warnings: list[str] = []

    reference_rows = {
        _row_key(row): row
        for row in reference_payload.get("results", [])
        if isinstance(row, dict)
    }
    candidate_rows = {
        _row_key(row): row
        for row in candidate_payload.get("results", [])
        if isinstance(row, dict)
    }
    if set(reference_rows) != set(candidate_rows):
        errors.append(
            "dataset/metric set mismatch: "
            f"reference={sorted(reference_rows)} candidate={sorted(candidate_rows)}"
        )

    for key in sorted(set(reference_rows) & set(candidate_rows)):
        dataset, metric = key
        ref_row = reference_rows[key]
        cand_row = candidate_rows[key]
        ref_score = _score(ref_row)
        cand_score = _score(cand_row)
        if ref_score is not None and cand_score is not None and abs(ref_score - cand_score) > score_tolerance:
            if _allows_generated_audio_score_drift(ref_row) and _allows_generated_audio_score_drift(cand_row):
                warnings.append(
                    f"generated-audio score drift for {dataset}/{metric}: "
                    f"reference={ref_score} candidate={cand_score}"
                )
            else:
                errors.append(f"score mismatch for {dataset}/{metric}: reference={ref_score} candidate={cand_score}")

        ref_nodes = _node_ids(ref_row)
        cand_nodes = _node_ids(cand_row)
        if ref_nodes != cand_nodes:
            errors.append(f"node mismatch for {dataset}/{metric}: reference={ref_nodes} candidate={cand_nodes}")

        ref_prediction = reference / "predictions" / f"{dataset}.txt"
        cand_prediction = candidate / "predictions" / f"{dataset}.txt"
        if ref_prediction.is_file() and cand_prediction.is_file():
            ref_keys = _prediction_keys(ref_prediction, sample_limit)
            cand_keys = _prediction_keys(cand_prediction, sample_limit)
            if ref_keys != cand_keys:
                errors.append(f"first {sample_limit} prediction keys mismatch for {dataset}: {ref_keys} != {cand_keys}")

        ref_pipeline = ref_row.get("pipeline") if isinstance(ref_row.get("pipeline"), dict) else {}
        cand_pipeline = cand_row.get("pipeline") if isinstance(cand_row.get("pipeline"), dict) else {}
        ref_pipeline_id = ref_row.get("pipeline_id") or ref_pipeline.get("pipeline_id")
        cand_pipeline_id = cand_row.get("pipeline_id") or cand_pipeline.get("pipeline_id")
        if ref_pipeline_id != cand_pipeline_id:
            warnings.append(f"pipeline_id differs for {dataset}/{metric}: {ref_pipeline_id} != {cand_pipeline_id}")

    return {
        "ok": not errors,
        "reference_run_dir": str(reference),
        "candidate_run_dir": str(candidate),
        "sample_limit": sample_limit,
        "score_tolerance": score_tolerance,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two main-flow SURE-EVAL artifact trees")
    parser.add_argument("--reference-run-dir", required=True)
    parser.add_argument("--candidate-run-dir", required=True)
    parser.add_argument("--sample-limit", type=int, default=5)
    parser.add_argument("--score-tolerance", type=float, default=1e-9)
    parser.add_argument("--output")
    args = parser.parse_args()

    report = compare_runs(
        Path(args.reference_run_dir).resolve(),
        Path(args.candidate_run_dir).resolve(),
        sample_limit=args.sample_limit,
        score_tolerance=args.score_tolerance,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
