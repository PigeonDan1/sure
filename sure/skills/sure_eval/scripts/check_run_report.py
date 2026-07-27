#!/usr/bin/env python3
"""Gate script for the RUN_REPORT_UNIT.

Verifies the final report is persisted, execution_path_actual is declared, and
completed runs contain the main-flow evaluation artifact tree. Called by the Sure
hook:
    python3 scripts/check_run_report.py --run-dir <runDir> --produces <abs>

exit 0 = pass; non-zero = fail (stderr carries the repair text).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SUCCESS_STATUSES = {"success", "succeeded", "ok", "completed", "complete"}
FAILURE_STATUSES = {"failed", "failure", "error"}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_run_roots(hook_run_dir: Path, report_path: Path, data: dict) -> list[Path]:
    candidates: list[Path] = []
    for key in ("evaluation_run_dir", "run_dir", "artifact_root", "eval_run_dir"):
        value = data.get(key)
        if isinstance(value, str) and value:
            candidates.append(Path(value))
    artifacts = data.get("artifacts")
    if isinstance(artifacts, dict):
        for key in ("evaluation_payload", "report_jsonl", "protocol", "metrics_dir", "sample_reports_dir"):
            value = artifacts.get(key)
            if isinstance(value, str) and value:
                path = Path(value)
                candidates.append(path if path.suffix == "" else path.parent)
    candidates.extend([hook_run_dir, hook_run_dir / "artifacts", report_path.parent, report_path.parent.parent])

    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return deduped


def _find_artifact_root(hook_run_dir: Path, report_path: Path, data: dict) -> Path | None:
    for candidate in _candidate_run_roots(hook_run_dir, report_path, data):
        if (candidate / "evaluation_payload.json").is_file() and (candidate / "report.jsonl").is_file():
            return candidate
    return None


def _metric_slug(metric: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in str(metric)) or "metric"


def _artifact_path(root: Path, value: Any, fallback: Path) -> Path:
    if isinstance(value, str) and value:
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        return path
    return fallback


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _validate_completed_artifacts(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in ("evaluation_payload.json", "report.jsonl", "protocol.yaml"):
        if not (root / relative).is_file():
            errors.append(f"missing required artifact: {root / relative}")

    payload_path = root / "evaluation_payload.json"
    payload: dict = {}
    if payload_path.is_file():
        try:
            payload = _read_json(payload_path)
        except json.JSONDecodeError as exc:
            errors.append(f"evaluation_payload.json is invalid JSON: {exc}")
        else:
            if payload.get("schema") != "sure.eval.payload.v2":
                errors.append("evaluation_payload.json must use schema sure.eval.payload.v2")
            if payload.get("evaluation_backend") == "legacy":
                errors.append("completed aligned run must not use legacy evaluation backend")

    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    if not results:
        errors.append("evaluation_payload.json results must be a non-empty list")

    report_lines: list[dict] = []
    report_path = root / "report.jsonl"
    if report_path.is_file():
        for index, line in enumerate(report_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                report_lines.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"report.jsonl line {index} is invalid JSON: {exc}")
    if results and len(report_lines) != len(results):
        errors.append(f"report.jsonl row count {len(report_lines)} does not match evaluation results {len(results)}")

    prediction_dir = root / "predictions"
    if not prediction_dir.is_dir():
        errors.append(f"missing predictions directory: {prediction_dir}")

    for index, row in enumerate(results):
        if not isinstance(row, dict):
            errors.append(f"results[{index}] must be an object")
            continue
        if row.get("schema") != "sure.eval.payload.dataset_metric.v2":
            errors.append(f"results[{index}] must use schema sure.eval.payload.dataset_metric.v2")
        if row.get("evaluation_backend") == "legacy":
            errors.append(f"results[{index}] uses legacy evaluation backend")
        dataset = str(row.get("dataset") or "")
        metric = str(row.get("metric") or "")
        pipeline_id = row.get("pipeline_id")
        if not dataset or not metric:
            errors.append(f"results[{index}] must declare dataset and metric")
            continue
        if not pipeline_id:
            errors.append(f"results[{index}] must declare pipeline_id")
        result = row.get("result")
        if not isinstance(result, dict) or "score" not in result:
            errors.append(f"results[{index}].result.score is required")
        for suffix in (".txt", ".jsonl"):
            path = prediction_dir / f"{dataset}{suffix}"
            if not path.is_file():
                errors.append(f"missing prediction file: {path}")
        slug = _metric_slug(metric)
        artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
        metric_dir = _artifact_path(root, artifacts.get("metric_artifact_dir"), root / "metrics" / dataset / slug)
        report_artifact = _artifact_path(root, artifacts.get("report"), metric_dir / "report.json")
        pipeline_artifact = _artifact_path(
            root,
            artifacts.get("pipeline_description"),
            metric_dir / "pipeline_description.json",
        )
        sample_report = _artifact_path(
            root,
            artifacts.get("sample_report"),
            root / "sample_reports" / dataset / f"{slug}.jsonl",
        )
        for label, path in (
            ("metric report", report_artifact),
            ("pipeline description", pipeline_artifact),
            ("sample report", sample_report),
        ):
            if not _is_under(path, root):
                errors.append(f"results[{index}] {label} path must stay under the run root: {path}")
            if not path.is_file():
                errors.append(f"missing {label}: {path}")
    return errors


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _execution_requested(data: dict[str, Any], run_dir: Path, report_path: Path) -> str:
    execution = data.get("execution")
    if isinstance(execution, dict) and isinstance(execution.get("requested"), str):
        return str(execution["requested"])
    for key in ("execution_path_requested", "execution_requested"):
        if isinstance(data.get(key), str):
            value = str(data[key])
            if value in {"local", "vc", "auto"}:
                return value
            if value == "vc_submit":
                return "vc"
            if value in {"local_bash", "local_docker"}:
                return "local"

    eval_input = _read_optional_json(run_dir / "artifacts" / "eval_input_resolved.json")
    runtime = eval_input.get("runtime") if isinstance(eval_input.get("runtime"), dict) else {}
    resolved_execution = runtime.get("execution") if isinstance(runtime.get("execution"), dict) else {}
    if isinstance(resolved_execution.get("requested"), str):
        return str(resolved_execution["requested"])

    execution_path_declared = str(data.get("execution_path_declared") or "")
    if execution_path_declared == "vc_submit":
        return "vc"
    if execution_path_declared in {"local_bash", "local_docker"}:
        return "local"
    return "auto"


def _submit_result(run_dir: Path, report_path: Path) -> dict[str, Any]:
    candidates = [
        run_dir / "artifacts" / "submit_result.json",
        report_path.parent / "submit_result.json",
    ]
    for candidate in candidates:
        payload = _read_optional_json(candidate)
        if payload:
            return payload
    return {}


def _is_failed_pre_submit(data: dict[str, Any]) -> bool:
    status = str(data.get("status") or "").lower()
    if status not in FAILURE_STATUSES:
        return False
    execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
    path_actual = str(execution.get("path_actual") or data.get("execution_path_actual") or "").lower()
    actual = str(execution.get("actual") or "").lower()
    failure_class = str(execution.get("failure_class") or data.get("failure_class") or "").lower()
    return (
        path_actual in {"blocked_before_submit", "not_submitted", "failed_before_submit"}
        or actual in {"not_submitted", "blocked_before_submit", "failed_before_submit"}
        or failure_class in {"smoke_test_failed", "failed_before_submit", "pre_submit_failed"}
    )


def _execution_result(run_dir: Path, report_path: Path) -> dict[str, Any]:
    candidates = [
        run_dir / "artifacts" / "execution_result.json",
        report_path.parent / "execution_result.json",
    ]
    for candidate in candidates:
        payload = _read_optional_json(candidate)
        if payload:
            return payload
    return {}


def _validate_failed_execution_report(run_dir: Path, report_path: Path, data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    execution_result = _execution_result(run_dir, report_path)
    if not execution_result:
        errors.append("failed run report requires artifacts/execution_result.json")
    else:
        job_status = str(execution_result.get("job_status") or execution_result.get("status") or "").lower()
        exit_code = execution_result.get("exit_code")
        if job_status in SUCCESS_STATUSES or exit_code == 0:
            errors.append("failed run report conflicts with successful execution_result.json")
        if data.get("execution_path_actual") == "vc_submit" and not _is_failed_pre_submit(data):
            vc_job_id = data.get("vc_job_id") or execution_result.get("vc_job_id")
            execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
            vc_job_id = vc_job_id or execution.get("vc_job_id")
            if not vc_job_id:
                errors.append("failed vc_submit run report requires vc_job_id")

    assessment = _read_optional_json(run_dir / "artifacts" / "assessment_report.json")
    if not assessment:
        assessment = _read_optional_json(report_path.parent / "assessment_report.json")
    if not assessment:
        errors.append("failed run report requires assessment_report.json")
    else:
        assessment_status = str(assessment.get("status") or "").lower()
        if assessment_status in SUCCESS_STATUSES:
            errors.append("failed run report conflicts with successful assessment_report.json")

    if not data.get("next_action"):
        errors.append("failed run report requires next_action describing the required repair or blocker")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--produces", required=True, help="absolute path to main_agent_run_report.json")
    args = parser.parse_args()

    path = Path(args.produces)
    if not path.exists():
        print(f"main_agent_run_report.json not found at {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"main_agent_run_report.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if not data.get("report_persisted"):
        print(
            "RUN_REPORT_UNIT gate: report_persisted must be true. Preview the report, "
            "obtain user confirmation, then persist it.",
            file=sys.stderr,
        )
        return 1

    execution_path_actual = data.get("execution_path_actual", "")
    if not execution_path_actual:
        print(
            "RUN_REPORT_UNIT gate: execution_path_actual must be declared "
            "(vc_submit / local_bash / local_docker).",
            file=sys.stderr,
        )
        return 1

    if execution_path_actual != "vc_submit":
        requested = _execution_requested(data, Path(args.run_dir), path)
        submit_result = _submit_result(Path(args.run_dir), path)
        vc_available = bool(data.get("vc_available", submit_result.get("vc_available", False)))
        fallback_approved = bool(data.get("fallback_approved", submit_result.get("fallback_approved", False)))
        fallback_reason = data.get("local_fallback_reason") or submit_result.get("local_fallback_reason", "")
        evaluation_only = bool(data.get("evaluation_only"))
        failed_pre_submit = _is_failed_pre_submit(data)
        if requested == "vc" and not failed_pre_submit:
            print(
                "RUN_REPORT_UNIT gate: user requested execution=vc, but "
                f"execution_path_actual={execution_path_actual}.",
                file=sys.stderr,
            )
            return 1
        if not failed_pre_submit and not evaluation_only and requested != "local" and not fallback_reason:
            print(
                "RUN_REPORT_UNIT gate: a non-vc execution path with execution=auto "
                "requires a non-empty local_fallback_reason unless evaluation_only=true.",
                file=sys.stderr,
            )
            return 1
        if not failed_pre_submit and not evaluation_only and requested == "auto" and vc_available and not fallback_approved:
            print(
                "RUN_REPORT_UNIT gate: execution=auto used local execution even though vc was available; "
                "set fallback_approved=true for this intentional override.",
                file=sys.stderr,
            )
            return 1

    status = str(data.get("status") or "").lower()
    if status in {"prediction_complete_evaluation_incomplete", "evaluation_failed"}:
        print(f'RUN_REPORT_UNIT gate: status "{status}" is not a completed evaluation.', file=sys.stderr)
        return 1

    if status in FAILURE_STATUSES:
        artifact_errors = _validate_failed_execution_report(Path(args.run_dir), path, data)
        if artifact_errors:
            print("RUN_REPORT_UNIT failed-run artifact gate failed:\n  - " + "\n  - ".join(artifact_errors), file=sys.stderr)
            return 1
    elif status in SUCCESS_STATUSES or data.get("report_persisted"):
        artifact_root = _find_artifact_root(Path(args.run_dir), path, data)
        if artifact_root is None:
            print(
                "RUN_REPORT_UNIT gate: could not locate a run artifact root with "
                "evaluation_payload.json and report.jsonl. Record run_dir or "
                "evaluation_run_dir in main_agent_run_report.json.",
                file=sys.stderr,
            )
            return 1
        artifact_errors = _validate_completed_artifacts(artifact_root)
        if artifact_errors:
            print("RUN_REPORT_UNIT artifact gate failed:\n  - " + "\n  - ".join(artifact_errors), file=sys.stderr)
            return 1

    print(f"run_report OK: execution_path_actual={execution_path_actual}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
