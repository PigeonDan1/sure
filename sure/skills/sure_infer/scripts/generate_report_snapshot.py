#!/usr/bin/env python3
"""Generate a run-local SURE-EVAL report snapshot.

The snapshot is the human-facing evaluation protocol and result summary. It is
derived from the run-local machine artifacts and does not run inference or
refresh the global report database.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml

        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _escape(value: Any) -> str:
    text = "N/A" if value in (None, "") else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _bt(value: Any) -> str:
    text = _escape(value)
    return "`N/A`" if text == "N/A" else f"`{text}`"


def _bool(value: Any) -> str:
    if value is True:
        return "True"
    if value is False:
        return "False"
    return "N/A"


def _metric_slug(metric: Any) -> str:
    slug = "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in str(metric or "").lower())
    return slug or "metric"


def _payload_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _is_standard_report_row(row: dict[str, Any]) -> bool:
    return row.get("schema") == "sure.eval.report.dataset_metric.v1"


def _dataset(row: dict[str, Any]) -> dict[str, Any]:
    if _is_standard_report_row(row):
        value = row.get("dataset")
        return value if isinstance(value, dict) else {}
    return {
        "name": row.get("dataset"),
        "task": row.get("task"),
        "language": row.get("language"),
        "jsonl_path": row.get("jsonl_path") or (row.get("inputs") or {}).get("jsonl_path") if isinstance(row.get("inputs"), dict) else row.get("jsonl_path"),
        "num_samples": row.get("num_samples"),
    }


def _metric(row: dict[str, Any]) -> dict[str, Any]:
    if _is_standard_report_row(row):
        value = row.get("metric")
        return value if isinstance(value, dict) else {}
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    score = result.get("score")
    score_key = result.get("score_key")
    if score is None and score_key:
        score = result.get(score_key)
    return {
        "name": row.get("metric"),
        "score": score,
        "unit": "score",
        "display": f"{float(score):.6f}" if isinstance(score, (int, float)) else "N/A",
        "higher_is_better": None,
        "score_key": score_key or "score",
    }


def _prediction(row: dict[str, Any]) -> dict[str, Any]:
    if _is_standard_report_row(row):
        value = row.get("prediction")
        return value if isinstance(value, dict) else {}
    artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
    inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
    return {"file": artifacts.get("prediction_file") or inputs.get("prediction_path"), "validation": {}}


def _pipeline(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("pipeline") if isinstance(row.get("pipeline"), dict) else {}
    if _is_standard_report_row(row):
        return value
    return {
        "pipeline_id": row.get("pipeline_id") or value.get("pipeline_id"),
        "report_path": value.get("report_path"),
        "description_path": value.get("description_path"),
        "nodes": value.get("nodes") or row.get("nodes") or [],
        "conversion_steps": value.get("conversion_steps") or [],
    }


def _artifacts(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
    return value


def _status(row: dict[str, Any]) -> str:
    return str(row.get("status") or "success")


def _node_label(node: Any) -> tuple[str, str, str]:
    if isinstance(node, dict):
        return (
            str(node.get("node_id") or node.get("name") or "N/A"),
            str(node.get("version") or node.get("node_version") or "N/A"),
            str(node.get("manifest") or node.get("manifest_path") or node.get("config_path") or "N/A"),
        )
    return (str(node or "N/A"), "N/A", "N/A")


def _rows_for_snapshot(payload: dict[str, Any], report_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if report_rows:
        return report_rows
    return _payload_rows(payload)


def _join_table(rows: list[str]) -> str:
    return "\n".join(rows) if rows else "| N/A | N/A | N/A | N/A | N/A | N/A |"


def _validation_value(validation: dict[str, Any], key: str, default: Any = None) -> Any:
    value = validation.get(key, default)
    if isinstance(value, list):
        return len(value)
    return value


def _build_dataset_scope(rows: list[dict[str, Any]]) -> str:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        ds = _dataset(row)
        metric = _metric(row)
        name = str(ds.get("name") or "")
        if not name:
            continue
        item = grouped.setdefault(
            name,
            {
                "task": ds.get("task"),
                "language": ds.get("language"),
                "metrics": [],
                "num_samples": ds.get("num_samples"),
                "jsonl_path": ds.get("jsonl_path"),
            },
        )
        metric_name = str(metric.get("name") or "")
        if metric_name and metric_name not in item["metrics"]:
            item["metrics"].append(metric_name)
    table = []
    for name, item in sorted(grouped.items()):
        table.append(
            f"| {_bt(name)} | {_escape(item.get('task'))} | {_escape(item.get('language'))} | "
            f"{_escape(', '.join(item['metrics']))} | {_escape(item.get('num_samples'))} | {_bt(item.get('jsonl_path'))} |"
        )
    return _join_table(table)


def _build_result_summary(rows: list[dict[str, Any]]) -> str:
    table = []
    for row in rows:
        ds = _dataset(row)
        metric = _metric(row)
        table.append(
            f"| {_bt(ds.get('name'))} | {_escape(ds.get('task'))} | {_escape(ds.get('language'))} | "
            f"{_escape(ds.get('num_samples'))} | {_bt(metric.get('name'))} | {_escape(metric.get('display'))} | {_escape(_status(row))} |"
        )
    return _join_table(table)


def _build_per_dataset_results(rows: list[dict[str, Any]]) -> str:
    table = []
    for row in rows:
        ds = _dataset(row)
        metric = _metric(row)
        validation = _prediction(row).get("validation") if isinstance(_prediction(row).get("validation"), dict) else {}
        table.append(
            f"| {_bt(ds.get('name'))} | {_bt(metric.get('name'))} | {_escape(metric.get('display'))} | "
            f"{_escape(metric.get('score'))} | {_escape(metric.get('unit'))} | {_bool(metric.get('higher_is_better'))} | "
            f"{_bool(validation.get('is_valid'))} |"
        )
    return _join_table(table)


def _build_metric_details(rows: list[dict[str, Any]]) -> str:
    table = []
    for row in rows:
        ds = _dataset(row)
        metric = _metric(row)
        table.append(
            f"| {_bt(ds.get('name'))} | {_bt(metric.get('name'))} | {_escape(metric.get('score_key'))} | "
            f"{_escape(metric.get('display'))} | {_escape(metric.get('score'))} | {_escape(metric.get('unit'))} | "
            f"{_bool(metric.get('higher_is_better'))} | {_escape(_status(row))} |"
        )
    return _join_table(table)


def _build_validation_summary(rows: list[dict[str, Any]]) -> str:
    table = []
    seen: set[str] = set()
    for row in rows:
        ds = _dataset(row)
        name = str(ds.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        validation = _prediction(row).get("validation") if isinstance(_prediction(row).get("validation"), dict) else {}
        table.append(
            f"| {_bt(name)} | {_escape(_validation_value(validation, 'expected_samples'))} | "
            f"{_escape(_validation_value(validation, 'provided_predictions'))} | "
            f"{_escape(_validation_value(validation, 'missing_keys', []))} | "
            f"{_escape(_validation_value(validation, 'extra_keys', []))} | "
            f"{_escape(_validation_value(validation, 'duplicate_keys', []))} | "
            f"{_escape(_validation_value(validation, 'empty_prediction_keys', []))} | {_bool(validation.get('is_valid'))} |"
        )
    return _join_table(table)


def _build_validation_details(rows: list[dict[str, Any]]) -> str:
    table = []
    seen: set[str] = set()
    for row in rows:
        ds = _dataset(row)
        name = str(ds.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        prediction = _prediction(row)
        validation = prediction.get("validation") if isinstance(prediction.get("validation"), dict) else {}
        violations = (
            len(validation.get("contract_violation_keys") or [])
            + len(validation.get("structured_projection_mismatch_keys") or [])
            + len(validation.get("invalid_structured_rows") or [])
        )
        notes = []
        if validation.get("structured_projection_mismatch_keys"):
            notes.append("txt/jsonl projection mismatch")
        if validation.get("contract_violation_keys"):
            notes.append("task contract violation")
        table.append(
            f"| {_bt(name)} | {_escape(validation.get('format_used'))} | {_bt(prediction.get('file'))} | "
            f"{_bt(validation.get('prediction_jsonl_path'))} | {_bool(validation.get('is_valid'))} | {violations} | "
            f"{_escape(', '.join(notes) or 'N/A')} |"
        )
    return _join_table(table)


def _build_evaluation_pipeline(rows: list[dict[str, Any]]) -> str:
    table = []
    for row in rows:
        ds = _dataset(row)
        metric = _metric(row)
        pipe = _pipeline(row)
        nodes = pipe.get("nodes") if isinstance(pipe.get("nodes"), list) else []
        if not nodes:
            table.append(
                f"| {_bt(ds.get('name'))} | {_bt(metric.get('name'))} | {_bt(pipe.get('pipeline_id'))} | route | N/A | N/A | N/A |"
            )
            continue
        for index, node in enumerate(nodes, 1):
            node_id, version, manifest = _node_label(node)
            table.append(
                f"| {_bt(ds.get('name'))} | {_bt(metric.get('name'))} | {_bt(pipe.get('pipeline_id'))} | "
                f"{index} | {_bt(node_id)} | {_escape(version)} | {_bt(manifest)} |"
            )
    return _join_table(table)


def _build_pipeline_trace(rows: list[dict[str, Any]]) -> str:
    table = []
    for row in rows:
        ds = _dataset(row)
        metric = _metric(row)
        pipe = _pipeline(row)
        nodes = pipe.get("nodes") if isinstance(pipe.get("nodes"), list) else []
        conversions = pipe.get("conversion_steps") if isinstance(pipe.get("conversion_steps"), list) else []
        table.append(
            f"| {_bt(ds.get('name'))} | {_bt(metric.get('name'))} | {_bt(pipe.get('pipeline_id'))} | "
            f"{_bt(pipe.get('report_path'))} | {_bt(pipe.get('description_path'))} | {len(conversions)} | {len(nodes)} |"
        )
    return _join_table(table)


def _build_runtime_versions(rows: list[dict[str, Any]], payload: dict[str, Any], protocol: dict[str, Any]) -> str:
    versions: dict[str, Any] = {
        "snapshot_generator": "scripts/generate_report_snapshot.py",
        "python": sys.version.split()[0],
        "evaluation_payload_schema": payload.get("schema"),
        "report_row_schema": "sure.eval.report.dataset_metric.v1" if rows and _is_standard_report_row(rows[0]) else "legacy_or_payload",
        "protocol_schema": protocol.get("schema"),
    }
    for row in rows:
        value = row.get("versions") if isinstance(row.get("versions"), dict) else {}
        for key, subvalue in value.items():
            if subvalue not in (None, "", [], {}):
                versions.setdefault(str(key), subvalue)
    return "\n".join(f"| {_escape(key)} | {_escape(value)} |" for key, value in sorted(versions.items()))


def _build_failed_or_skipped(rows: list[dict[str, Any]]) -> str:
    table = []
    for row in rows:
        status = _status(row).lower()
        if status not in {"success", "succeeded", "ok", "completed", "complete"}:
            ds = _dataset(row)
            metric = _metric(row)
            artifacts = _artifacts(row)
            table.append(
                f"| {_bt(ds.get('name'))} | {_bt(metric.get('name'))} | {_escape(status)} | "
                f"{_escape(row.get('reason'))} | {_bt(artifacts.get('report') or artifacts.get('metric_artifact_dir'))} |"
            )
    return "\n".join(table) if table else "| N/A | N/A | N/A | N/A | N/A |"


def _build_output_artifacts(rows: list[dict[str, Any]], run_dir: Path) -> str:
    table = [
        f"| evaluation_payload | all | all | {_bt(run_dir / 'evaluation_payload.json')} |",
        f"| report_jsonl | all | all | {_bt(run_dir / 'report.jsonl')} |",
        f"| report_snapshot | all | all | {_bt(run_dir / 'report_snapshot.md')} |",
        f"| protocol | all | all | {_bt(run_dir / 'protocol.yaml')} |",
        f"| prediction_manifest | all | all | {_bt(run_dir / 'predictions' / 'manifest.json')} |",
        f"| conversion_manifest | all | all | {_bt(run_dir / 'predictions' / 'conversion_manifest.json')} |",
    ]
    for row in rows:
        ds = _dataset(row)
        metric = _metric(row)
        prediction = _prediction(row)
        artifacts = _artifacts(row)
        for label, value in (
            ("prediction_file", prediction.get("file")),
            ("prediction_jsonl", (prediction.get("validation") or {}).get("prediction_jsonl_path") if isinstance(prediction.get("validation"), dict) else None),
            ("metric_artifact_dir", artifacts.get("metric_artifact_dir")),
            ("metric_report", artifacts.get("report")),
            ("pipeline_description", artifacts.get("pipeline_description")),
            ("sample_report", artifacts.get("sample_report")),
        ):
            if value:
                table.append(f"| {_escape(label)} | {_bt(ds.get('name'))} | {_bt(metric.get('name'))} | {_bt(value)} |")
    return "\n".join(table)


def build_snapshot(run_dir: Path) -> str:
    payload_path = run_dir / "evaluation_payload.json"
    report_path = run_dir / "report.jsonl"
    protocol_path = run_dir / "protocol.yaml"
    validation_path = run_dir / "validation_payload.json"
    payload = _read_json(payload_path)
    report_rows = _read_jsonl(report_path)
    protocol = _read_yaml(protocol_path)
    rows = _rows_for_snapshot(payload, report_rows)

    datasets = sorted({str(_dataset(row).get("name") or "") for row in rows if _dataset(row).get("name")})
    metrics = sorted({str(_metric(row).get("name") or "") for row in rows if _metric(row).get("name")})
    tasks = sorted({str(_dataset(row).get("task") or "") for row in rows if _dataset(row).get("task")})
    model_values = [
        (_is_standard_report_row(row) and isinstance(row.get("model"), dict) and row["model"].get("model_name"))
        for row in rows
    ]
    model_name = next((str(value) for value in model_values if value), "unknown_model")
    model_source = protocol.get("model", {}).get("model_source") if isinstance(protocol.get("model"), dict) else None
    protocol_run = protocol.get("run") if isinstance(protocol.get("run"), dict) else {}
    run_id = str(protocol_run.get("run_id") or run_dir.name)
    status = "success" if rows and all(_status(row).lower() in {"success", "succeeded", "ok", "completed", "complete"} for row in rows) else "incomplete"
    require_nonempty = any(
        bool((_prediction(row).get("validation") or {}).get("require_nonempty"))
        for row in rows
        if isinstance(_prediction(row).get("validation"), dict)
    )

    lines = [
        f"# {model_name} Evaluation Snapshot",
        "",
        "## Basic Information",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Model name | {_bt(model_name)} |",
        f"| Model source | {_escape(model_source)} |",
        f"| Task | {_escape(', '.join(tasks) or 'N/A')} |",
        f"| Dataset scope | {_bt(', '.join(datasets) or 'N/A')} |",
        f"| Run ID | {_bt(run_id)} |",
        f"| Output directory | {_bt(run_dir)} |",
        f"| Standard results mirror | {_bt('N/A')} |",
        f"| Report JSONL | {_bt(report_path)} |",
        f"| Evaluation payload | {_bt(payload_path)} |",
        f"| Validation payload | {_bt(validation_path if validation_path.exists() else 'N/A')} |",
        f"| Status | {_escape(status)} |",
        "",
        "## Formatting Policy",
        "",
        "- Numeric display values in human-facing tables should use two decimal places unless the field is an identifier, version, path, timestamp, hash, or raw JSON-derived string.",
        "- Percent scores should be rendered with a `%` suffix, for example `1.58%`.",
        "- Raw fraction scores should be rendered as decimals, for example `0.02`.",
        "- Missing optional values should be rendered as `N/A`.",
        "- Boolean values should be rendered as `True` or `False`.",
        "- Paths, dataset names, model names, run IDs, pipeline IDs, node IDs, and metric names should be wrapped in backticks.",
        "- Tables should preserve one row per dataset-metric unless the section explicitly describes nodes or artifacts.",
        "",
        "## Evaluation Scope",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Result granularity | dataset_metric |",
        f"| Report row count | {len(rows)} |",
        f"| Dataset count | {len(datasets)} |",
        f"| Metric count | {len(metrics)} |",
        "| Sample-level result layout | `sample_reports/<dataset>/<metric_slug>.jsonl` |",
        "| Metric artifact layout | `metrics/<dataset>/<metric_slug>/` |",
        "| Report row rule | one row per dataset metric |",
        "",
        "## Dataset Scope",
        "",
        "| Dataset | Task | Lang | Metrics | Samples | JSONL |",
        "|---|---:|---:|---|---:|---|",
        _build_dataset_scope(rows),
        "",
        "## Result Summary",
        "",
        "| Artifact | Path |",
        "|---|---|",
        f"| Run-local result file | {_bt(report_path)} |",
        f"| Standard result mirror | {_bt('N/A')} |",
        "",
        "| Dataset | Task | Lang | Samples | Metric | Score | Status |",
        "|---|---:|---:|---:|---:|---:|---|",
        _build_result_summary(rows),
        "",
        "## Result Field Notes",
        "",
        "| Field | Meaning | Format |",
        "|---|---|---|",
        "| `Dataset` | Evaluated dataset identifier | Backticked string |",
        "| `Task` | Dataset task family | Short uppercase task name |",
        "| `Lang` | Dataset language used for route selection | ISO-like short language code |",
        "| `Samples` | Number of evaluated samples | Two-decimal number |",
        "| `Metric` | Metric selected for the dataset | Uppercase display name |",
        "| `Score` | Display score for the selected metric | Two-decimal percent for error-rate metrics |",
        "| `Status` | Dataset-metric evaluation status | `success`, `failed`, or `skipped` |",
        "",
        "Notes:",
        "",
        "- ASR/TTS/VC error-rate metrics such as WER and CER are lower-is-better.",
        "- `Score` should be derived from `metric.display` when available.",
        "- Keep this summary table to the listed columns only.",
        "",
        "## Per-Dataset Test Results",
        "",
        f"The standard mirror report contains {len(rows)} dataset-metric rows.",
        "",
        "| Dataset | Metric | Display Score | Raw Score | Unit | Higher Is Better | Prediction Validation |",
        "|---|---:|---:|---:|---|---:|---|",
        _build_per_dataset_results(rows),
        "",
        "## Metric Details",
        "",
        "| Dataset | Metric | Score Key | Display Score | Raw Score | Unit | Higher Is Better | Status |",
        "|---|---:|---|---:|---:|---|---:|---|",
        _build_metric_details(rows),
        "",
        "## Validation Summary",
        "",
        "| Dataset | Expected | Provided | Missing | Extra | Duplicate | Empty | Valid |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
        _build_validation_summary(rows),
        "",
        "## Validation Details",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Prediction contract | `references/contracts/prediction_output_contract.md` |",
        "| Compatibility prediction file | `predictions/<dataset>.txt` |",
        "| Structured prediction file | `predictions/<dataset>.jsonl` |",
        f"| Required non-empty predictions | {_bool(require_nonempty)} |",
        "",
        "Validation checks:",
        "",
        "- Expected sample count",
        "- Provided prediction count",
        "- Missing keys",
        "- Extra keys",
        "- Duplicate keys",
        "- Empty predictions",
        "- Structured missing keys",
        "- Structured extra keys",
        "- Structured duplicate keys",
        "- Invalid structured rows",
        "- TSV/JSONL normalized prediction mismatches",
        "- Contract violation keys",
        "",
        "| Dataset | Format | Prediction TXT | Prediction JSONL | Structured Valid | Contract Violations | Notes |",
        "|---|---|---|---|---:|---:|---|",
        _build_validation_details(rows),
        "",
        "## Evaluation Pipeline",
        "",
        "| Dataset | Metric | Pipeline ID | Stage | Node | Node Version | Manifest |",
        "|---|---:|---|---|---|---|---|",
        _build_evaluation_pipeline(rows),
        "",
        "## Pipeline Trace Details",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Evaluation entrypoint | `scripts/evaluate_predictions.py` |",
        "| Metric router | `sure_eval.evaluation.scripts.run_task(...)` |",
        "| Route source | `src/sure_eval/evaluation/tasks/<task>/routes.yaml` |",
        "| Metric report layout | `metrics/<dataset>/<metric_slug>/report.json` |",
        "| Pipeline description layout | `metrics/<dataset>/<metric_slug>/pipeline_description.json` |",
        "| Conversion trace policy | preserved when `conversion_steps` or `conversion_trace` exists |",
        "",
        "| Dataset | Metric | Pipeline ID | Report | Description | Conversion Steps | Node Count |",
        "|---|---:|---|---|---|---:|---:|",
        _build_pipeline_trace(rows),
        "",
        "## Evaluation Runtime And Tool Versions",
        "",
        "| Component | Version / Path |",
        "|---|---|",
        _build_runtime_versions(rows, payload, protocol),
        "",
        "## Failed Or Skipped Evaluations",
        "",
        "| Dataset | Metric | Status | Reason | Artifact |",
        "|---|---:|---|---|---|",
        _build_failed_or_skipped(rows),
        "",
        "## Output Artifacts",
        "",
        "| Artifact | Dataset | Metric | Path |",
        "|---|---|---:|---|",
        _build_output_artifacts(rows, run_dir),
        "",
        "## Artifact Groups",
        "",
        "| Group | Contents |",
        "|---|---|",
        "| Report artifacts | `report.jsonl`, `evaluation_payload.json`, `report_snapshot.md` |",
        "| Validation artifacts | `validation_payload.json`, prediction validation summaries |",
        "| Prediction artifacts | `predictions/<dataset>.txt`, `predictions/<dataset>.jsonl`, `predictions/manifest.json`, `predictions/conversion_manifest.json` |",
        "| Metric artifacts | Route-backed per-dataset metric reports under `metrics/<dataset>/<metric_slug>/` |",
        "| Sample reports | Per-sample metric reports under `sample_reports/<dataset>/<metric_slug>.jsonl` |",
        "",
        "## Test Notes",
        "",
        "- Generated by `scripts/generate_report_snapshot.py` from run-local artifacts.",
        "- `protocol.yaml` records inference protocol only; this snapshot records evaluation protocol and result interpretation.",
        "- `evaluation_payload.json` remains the harness internal payload; `report.jsonl` is the standard dataset-metric report.",
        f"- Generated at `{datetime.now(timezone.utc).isoformat()}`.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a run-local SURE-EVAL report snapshot")
    parser.add_argument("--run-dir", required=True, help="Run directory containing evaluation_payload.json")
    parser.add_argument("--output", help="Markdown output path; defaults to <run-dir>/report_snapshot.md")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    output = Path(args.output).resolve() if args.output else run_dir / "report_snapshot.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_snapshot(run_dir), encoding="utf-8")
    print(json.dumps({"snapshot": str(output), "run_dir": str(run_dir)}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
