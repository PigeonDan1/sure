#!/usr/bin/env python3
"""Generate a run-local SURE-EVAL report snapshot.

This is the harness-compatible entrypoint used by the main-flow shell
templates. It summarizes artifacts already written under a run directory; it
does not refresh the global report database.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


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


def _display_score(row: dict[str, Any]) -> str:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    score = result.get("score")
    score_key = result.get("score_key")
    if score is None and score_key:
        score = result.get(score_key)
    if isinstance(score, (int, float)):
        return f"{float(score):.6f}"
    return "n/a"


def _metric_rows(payload: dict[str, Any], report_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    if rows:
        return [row for row in rows if isinstance(row, dict)]
    return report_rows


def build_snapshot(run_dir: Path) -> str:
    payload_path = run_dir / "evaluation_payload.json"
    report_path = run_dir / "report.jsonl"
    protocol_path = run_dir / "protocol.yaml"
    payload = _read_json(payload_path)
    report_rows = _read_jsonl(report_path)
    rows = _metric_rows(payload, report_rows)

    lines = [
        "# SURE-EVAL Report Snapshot",
        "",
        f"- generated_at: {datetime.now(timezone.utc).isoformat()}",
        f"- run_dir: {run_dir}",
        f"- evaluation_payload: {payload_path}",
        f"- report_jsonl: {report_path}",
        f"- protocol: {protocol_path}",
        "",
        "## Dataset Metrics",
        "",
    ]
    if not rows:
        lines.append("No dataset metric rows were found.")
    else:
        lines.append("| Dataset | Task | Language | Metric | Score | Pipeline |")
        lines.append("| --- | --- | --- | --- | ---: | --- |")
        for row in rows:
            pipeline = row.get("pipeline") if isinstance(row.get("pipeline"), dict) else {}
            pipeline_id = row.get("pipeline_id") or pipeline.get("pipeline_id") or "n/a"
            lines.append(
                "| {dataset} | {task} | {language} | {metric} | {score} | {pipeline} |".format(
                    dataset=row.get("dataset", "n/a"),
                    task=row.get("task", "n/a"),
                    language=row.get("language", "n/a"),
                    metric=row.get("metric", "n/a"),
                    score=_display_score(row),
                    pipeline=pipeline_id,
                )
            )

    lines.extend(["", "## Artifacts", ""])
    for row in rows:
        dataset = row.get("dataset", "dataset")
        metric = row.get("metric", "metric")
        artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
        pipeline = row.get("pipeline") if isinstance(row.get("pipeline"), dict) else {}
        lines.append(f"- {dataset} / {metric}")
        for label, value in (
            ("metric_artifact_dir", artifacts.get("metric_artifact_dir")),
            ("report", artifacts.get("report") or pipeline.get("report_path")),
            ("pipeline_description", artifacts.get("pipeline_description") or pipeline.get("description_path")),
            ("sample_report", artifacts.get("sample_report")),
            ("prediction_file", artifacts.get("prediction_file")),
        ):
            if value:
                lines.append(f"  - {label}: `{value}`")
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
