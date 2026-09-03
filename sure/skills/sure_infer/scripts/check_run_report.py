#!/usr/bin/env python3
"""Gate script for the RUN_REPORT_UNIT.

Verifies the final report is persisted, execution_path_actual is declared, and
completed runs contain the artifact tree of their profile. Called by the Sure
hook:
    python3 scripts/check_run_report.py --run-dir <runDir> --produces <abs> [--profile infer|eval]

--profile infer (the /sure_infer run report) expects the inference product:
prediction_generation_status.json, protocol.yaml, predictions/<dataset>.txt and
references/sure_benchmark/jsonl/<dataset>.jsonl. --profile eval (default)
expects the evaluation tree: evaluation_payload.json, report.jsonl, metrics and
sample reports.

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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {index} is invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"line {index} must be a JSON object")
        rows.append(value)
    return rows


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml

        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(str(exc)) from exc
    return value if isinstance(value, dict) else {}


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


def _find_artifact_root(hook_run_dir: Path, report_path: Path, data: dict, infer_profile: bool = False) -> Path | None:
    candidates = _candidate_run_roots(hook_run_dir, report_path, data)
    if infer_profile:
        product_dir = str(_execution_result(hook_run_dir, report_path).get("product_dir") or "")
        if product_dir:
            candidates.insert(0, Path(product_dir).expanduser().resolve())
    for candidate in candidates:
        if infer_profile:
            if (candidate / "protocol.yaml").is_file() and (candidate / "prediction_generation_status.json").is_file():
                return candidate
        elif (candidate / "evaluation_payload.json").is_file() and (candidate / "report.jsonl").is_file():
            return candidate
    return None


def _metric_slug(metric: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in str(metric)) or "metric"


def _load_prediction_txt(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if "\t" in line:
                key, value = line.split("\t", 1)
            else:
                parts = line.split(None, 1)
                key = parts[0]
                value = parts[1] if len(parts) > 1 else ""
            rows[key] = value
    return rows


def _load_prediction_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path} line {index} must be an object")
            key = str(value.get("key") or "")
            if not key:
                raise ValueError(f"{path} line {index} missing key")
            rows[key] = value
    return rows


def _validate_prediction_projection(txt_path: Path, jsonl_path: Path) -> list[str]:
    errors: list[str] = []
    txt_rows = _load_prediction_txt(txt_path)
    jsonl_rows = _load_prediction_jsonl(jsonl_path)
    if set(txt_rows) != set(jsonl_rows):
        missing = sorted(set(txt_rows) - set(jsonl_rows))[:10]
        extra = sorted(set(jsonl_rows) - set(txt_rows))[:10]
        errors.append(f"prediction txt/jsonl key mismatch for {txt_path.name}: missing_jsonl={missing} extra_jsonl={extra}")
    for key in sorted(set(txt_rows) & set(jsonl_rows)):
        normalized = str(jsonl_rows[key].get("normalized_prediction") or "")
        if txt_rows[key] != normalized:
            errors.append(f"prediction txt/jsonl normalized mismatch for {txt_path.name} key={key}")
            break
    return errors


def _validate_protocol(root: Path) -> list[str]:
    path = root / "protocol.yaml"
    if not path.is_file():
        return []
    errors: list[str] = []
    try:
        protocol = _read_yaml(path)
    except ValueError as exc:
        return [f"protocol.yaml is invalid YAML: {exc}"]
    if protocol.get("schema") != "sure.eval.inference_protocol.v1":
        errors.append("protocol.yaml must use schema sure.eval.inference_protocol.v1")
    forbidden_eval_fields = {"datasets", "artifact_layout", "metrics", "evaluation", "results"}
    present = sorted(field for field in forbidden_eval_fields if field in protocol)
    if present:
        errors.append(f"protocol.yaml must stay inference-only; remove evaluation fields: {present}")
    for section in (
        "run",
        "model",
        "protocol_selection",
        "inference_environment",
        "inference_constraints",
        "inference_parameters",
        "execution_surface",
        "prediction_reuse",
        "prediction_contract",
        "provenance",
        "notes",
    ):
        if section not in protocol:
            errors.append(f"protocol.yaml missing section: {section}")
    inference_parameters = protocol.get("inference_parameters") if isinstance(protocol.get("inference_parameters"), dict) else {}
    prediction_reuse = protocol.get("prediction_reuse") if isinstance(protocol.get("prediction_reuse"), dict) else {}
    if not isinstance(inference_parameters.get("source_priority"), list) or not inference_parameters.get("source_priority"):
        errors.append("protocol.yaml inference_parameters.source_priority is required")
    argument_policy = inference_parameters.get("argument_policy") if isinstance(inference_parameters.get("argument_policy"), dict) else {}
    if not argument_policy and not prediction_reuse.get("enabled"):
        errors.append("protocol.yaml inference_parameters.argument_policy is required for generated predictions")
    provenance = protocol.get("provenance") if isinstance(protocol.get("provenance"), dict) else {}
    inference_environment = protocol.get("inference_environment") if isinstance(protocol.get("inference_environment"), dict) else {}
    runtime_kind = str(inference_environment.get("runtime_kind") or "container")
    container = inference_environment.get("container") if isinstance(inference_environment.get("container"), dict) else {}
    model_runtime = inference_environment.get("model_runtime") if isinstance(inference_environment.get("model_runtime"), dict) else {}
    runtime_inventory = inference_environment.get("runtime_inventory") if isinstance(inference_environment.get("runtime_inventory"), dict) else {}
    harness_runtime = inference_environment.get("harness_runtime") if isinstance(inference_environment.get("harness_runtime"), dict) else {}
    mount_policy = inference_environment.get("mount_policy") if isinstance(inference_environment.get("mount_policy"), dict) else {}
    if runtime_kind == "container":
        if "@sha256:" not in str(container.get("image_ref") or ""):
            errors.append("protocol.yaml container.image_ref must be digest-pinned")
        if container.get("execution_mode") != "container_only":
            errors.append("protocol.yaml container.execution_mode must be container_only")
        if container.get("host_python_fallback") is not False:
            errors.append("protocol.yaml must disable container host_python_fallback")
    elif runtime_kind == "python":
        for key in ("runtime_id", "python_executable", "lock_sha256", "manifest_sha256"):
            if not model_runtime.get(key):
                errors.append(f"protocol.yaml model_runtime.{key} is required for Python inference")
        if model_runtime.get("execution_mode") != "python":
            errors.append("protocol.yaml model_runtime.execution_mode must be python")
        if model_runtime.get("host_python_fallback") is not False:
            errors.append("protocol.yaml must disable Python host fallback")
    else:
        errors.append(f"protocol.yaml inference_environment.runtime_kind is unsupported: {runtime_kind}")
    if runtime_inventory.get("schema") != "sure.onboard.runtime_inventory.v2":
        errors.append("protocol.yaml must record runtime_inventory schema v2")
    if not prediction_reuse.get("enabled"):
        if harness_runtime.get("schema") != "sure.harness.runtime.binding.v1":
            errors.append("protocol.yaml must record the common Harness Runtime binding")
        for key in ("runtime_id", "python_executable", "lock_sha256", "manifest_path", "runtime_root"):
            if not harness_runtime.get(key):
                errors.append(f"protocol.yaml harness_runtime.{key} is required for generated predictions")
    if runtime_kind == "container" and mount_policy.get("nfs_models_read_only") is not True:
        errors.append("protocol.yaml must record a read-only NFS model mount")
    if runtime_kind == "python":
        if mount_policy.get("nfs_models_read_only") is not False:
            errors.append("protocol.yaml must accurately record the trusted-host Python model mount")
        if mount_policy.get("model_integrity") != "verify_before_after":
            errors.append("protocol.yaml must record before/after model integrity verification")
    if provenance.get("raw_response_source_of_truth") is not False:
        errors.append("protocol.yaml provenance.raw_response_source_of_truth must be false")
    if prediction_reuse.get("enabled"):
        if prediction_reuse.get("generation_policy") != "reused_predictions_no_inference":
            errors.append("protocol.yaml prediction_reuse.generation_policy must be reused_predictions_no_inference")
        if prediction_reuse.get("old_evaluation_reused") is not False:
            errors.append("protocol.yaml prediction_reuse.old_evaluation_reused must be false")
    elif not provenance.get("prediction_generation_status"):
        errors.append("protocol.yaml provenance.prediction_generation_status is required for generated predictions")
    if not provenance.get("deployment_ready") or not provenance.get("package_gate"):
        errors.append("protocol.yaml provenance must link deployment_ready and package_gate")
    return errors


def _validate_report_rows(root: Path, expected_count: int) -> list[str]:
    path = root / "report.jsonl"
    if not path.is_file():
        return []
    errors: list[str] = []
    try:
        rows = _read_jsonl(path)
    except ValueError as exc:
        return [f"report.jsonl is invalid: {exc}"]
    if expected_count and len(rows) != expected_count:
        errors.append(f"report.jsonl row count {len(rows)} does not match evaluation results {expected_count}")
    for index, row in enumerate(rows, 1):
        if row.get("schema") != "sure.eval.report.dataset_metric.v1":
            errors.append(f"report.jsonl line {index} must use schema sure.eval.report.dataset_metric.v1")
            continue
        for section in ("run", "model", "dataset", "prediction", "metric", "pipeline", "artifacts", "status"):
            if section not in row:
                errors.append(f"report.jsonl line {index} missing section {section}")
        metric = row.get("metric") if isinstance(row.get("metric"), dict) else {}
        if metric and "higher_is_better" not in metric:
            errors.append(f"report.jsonl line {index} metric.higher_is_better is required")
    return errors


def _validate_prediction_manifests(root: Path, datasets: set[str]) -> list[str]:
    errors: list[str] = []
    pred_dir = root / "predictions"
    manifest_path = pred_dir / "manifest.json"
    conversion_path = pred_dir / "conversion_manifest.json"
    for path, schema in (
        (manifest_path, "sure.eval.prediction_manifest.v1"),
        (conversion_path, "sure.eval.prediction_conversion_manifest.v1"),
    ):
        if not path.is_file():
            errors.append(f"missing prediction manifest: {path}")
            continue
        try:
            payload = _read_json(path)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name} is invalid JSON: {exc}")
            continue
        if payload.get("schema") != schema:
            errors.append(f"{path.name} must use schema {schema}")
        rows = payload.get("datasets") if isinstance(payload.get("datasets"), list) else []
        covered = {str(item.get("dataset")) for item in rows if isinstance(item, dict) and item.get("dataset")}
        missing = sorted(datasets - covered)
        if missing:
            errors.append(f"{path.name} missing dataset coverage: {missing}")
    return errors


def _validate_snapshot(root: Path) -> list[str]:
    path = root / "report_snapshot.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    required = [
        "## Basic Information",
        "## Formatting Policy",
        "## Evaluation Scope",
        "## Dataset Scope",
        "## Result Summary",
        "## Per-Dataset Test Results",
        "## Metric Details",
        "## Validation Summary",
        "## Evaluation Pipeline",
        "## Pipeline Trace Details",
        "## Evaluation Runtime And Tool Versions",
        "## Output Artifacts",
        "## Artifact Groups",
        "## Test Notes",
    ]
    return [f"report_snapshot.md missing required section: {section}" for section in required if section not in text]


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
    for relative in ("evaluation_payload.json", "report.jsonl", "protocol.yaml", "report_snapshot.md"):
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

    errors.extend(_validate_protocol(root))
    errors.extend(_validate_report_rows(root, len(results)))
    errors.extend(_validate_snapshot(root))

    prediction_dir = root / "predictions"
    if not prediction_dir.is_dir():
        errors.append(f"missing predictions directory: {prediction_dir}")

    datasets: set[str] = set()
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
        datasets.add(dataset)
        if not pipeline_id:
            errors.append(f"results[{index}] must declare pipeline_id")
        result = row.get("result")
        if not isinstance(result, dict) or "score" not in result:
            errors.append(f"results[{index}].result.score is required")
        prediction_txt = prediction_dir / f"{dataset}.txt"
        prediction_jsonl = prediction_dir / f"{dataset}.jsonl"
        for path in (prediction_txt, prediction_jsonl):
            if not path.is_file():
                errors.append(f"missing prediction file: {path}")
        if prediction_txt.is_file() and prediction_jsonl.is_file():
            try:
                errors.extend(_validate_prediction_projection(prediction_txt, prediction_jsonl))
            except Exception as exc:
                errors.append(f"failed to validate prediction projection for {dataset}: {exc}")
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
    if prediction_dir.is_dir() and datasets:
        errors.extend(_validate_prediction_manifests(root, datasets))
    return errors


def _nonempty_prediction_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for value in _load_prediction_txt(path).values() if value.strip())


def _validate_infer_artifacts(root: Path) -> list[str]:
    """The /sure_infer product: generated predictions, protocol.yaml and the reference projections."""
    status_path = root / "prediction_generation_status.json"
    if not status_path.is_file():
        return [f"missing required artifact: {status_path}"]
    try:
        status = _read_json(status_path)
    except json.JSONDecodeError as exc:
        return [f"prediction_generation_status.json is invalid JSON: {exc}"]
    errors: list[str] = []
    rows = [row for row in status.get("datasets", []) if isinstance(row, dict)] if isinstance(status.get("datasets"), list) else []
    if not rows:
        errors.append("prediction_generation_status.json datasets must be a non-empty list")
    if not (root / "protocol.yaml").is_file():
        errors.append(f"missing required artifact: {root / 'protocol.yaml'}")
    for row in rows:
        dataset = str(row.get("dataset") or "")
        if not dataset:
            errors.append("prediction_generation_status.json rows must name their dataset")
            continue
        if row.get("status") != "completed":
            errors.append(f"prediction_generation_status.json marks {dataset} as {row.get('status')!r}, not completed")
        prediction = root / "predictions" / f"{dataset}.txt"
        if _nonempty_prediction_rows(prediction) < 1:
            errors.append(f"missing or empty prediction file: {prediction}")
        reference = root / "references" / "sure_benchmark" / "jsonl" / f"{dataset}.jsonl"
        if not reference.is_file():
            errors.append(f"missing reference projection: {reference}")
    return errors


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


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


def _validate_completed_execution(run_dir: Path, report_path: Path) -> list[str]:
    """The mirror of the failed-report check: a success must not contradict the run.

    Only the failure direction was ever checked, so a report claiming success was
    accepted next to an execution_result.json recording a non-zero exit. One run
    is on record as "status": "success" with "ERROR: Evaluation exited with code 1"
    at the end of its job log.
    """
    execution_result = _execution_result(run_dir, report_path)
    if not execution_result:
        return []
    errors: list[str] = []
    job_status = str(execution_result.get("job_status") or execution_result.get("status") or "").lower()
    if job_status in FAILURE_STATUSES:
        errors.append(f'successful run report conflicts with execution_result.json job_status "{job_status}"')
    exit_code = execution_result.get("exit_code")
    if isinstance(exit_code, (int, float)) and not isinstance(exit_code, bool) and int(exit_code) != 0:
        errors.append(f"successful run report conflicts with execution_result.json exit_code {int(exit_code)}")
    return errors


def _validate_failed_execution_report(
    run_dir: Path, report_path: Path, data: dict[str, Any], *, require_assessment: bool = True
) -> list[str]:
    errors: list[str] = []
    execution_result = _execution_result(run_dir, report_path)
    failed_pre_submit = _is_failed_pre_submit(data)
    if not execution_result and not failed_pre_submit:
        errors.append("failed run report requires artifacts/execution_result.json")
    elif not execution_result:
        smoke = _read_optional_json(run_dir / "artifacts" / "smoke_test_result.json")
        readiness = _read_optional_json(run_dir / "artifacts" / "execution_readiness_report.json")
        smoke_failed = bool(smoke) and smoke.get("smoke_passed") is False
        readiness_failed = bool(readiness) and readiness.get("execution_ready") is False
        if not smoke_failed and not readiness_failed:
            errors.append(
                "failed pre-submit report requires a failed smoke_test_result.json "
                "or execution_readiness_report.json"
            )
    else:
        job_status = str(execution_result.get("job_status") or execution_result.get("status") or "").lower()
        exit_code = execution_result.get("exit_code")
        if job_status in SUCCESS_STATUSES or exit_code == 0:
            errors.append("failed run report conflicts with successful execution_result.json")

    if require_assessment:
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
    parser.add_argument(
        "--profile",
        choices=("infer", "eval"),
        default="eval",
        help="which product tree a completed run must carry: the inference product or the evaluation tree",
    )
    args = parser.parse_args()
    infer_profile = args.profile == "infer"

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
    allowed_execution_paths = {
        "local_bash",
        "local_docker",
        "local_python",
        "blocked_before_submit",
        "not_submitted",
        "failed_before_submit",
    }
    if execution_path_actual not in allowed_execution_paths:
        print(
            "RUN_REPORT_UNIT gate: execution_path_actual must be one of "
            "local_bash / local_docker / local_python.",
            file=sys.stderr,
        )
        return 1

    evaluation_only = bool(data.get("evaluation_only"))
    failed_pre_submit = _is_failed_pre_submit(data)
    if execution_path_actual == "local_bash" and not evaluation_only and not failed_pre_submit:
        print(
            "RUN_REPORT_UNIT gate: formal model inference cannot use local_bash; use the approved local runtime.",
            file=sys.stderr,
        )
        return 1

    status = str(data.get("status") or "").lower()
    if status in {"prediction_complete_evaluation_incomplete", "evaluation_failed"}:
        print(f'RUN_REPORT_UNIT gate: status "{status}" is not a completed evaluation.', file=sys.stderr)
        return 1

    if status in FAILURE_STATUSES:
        artifact_errors = _validate_failed_execution_report(
            Path(args.run_dir), path, data, require_assessment=not infer_profile
        )
        if artifact_errors:
            print("RUN_REPORT_UNIT failed-run artifact gate failed:\n  - " + "\n  - ".join(artifact_errors), file=sys.stderr)
            return 1
    elif status in SUCCESS_STATUSES or data.get("report_persisted"):
        execution_errors = _validate_completed_execution(Path(args.run_dir), path)
        if execution_errors:
            print(
                "RUN_REPORT_UNIT completed-run execution gate failed:\n  - " + "\n  - ".join(execution_errors),
                file=sys.stderr,
            )
            return 1
        artifact_root = _find_artifact_root(Path(args.run_dir), path, data, infer_profile=infer_profile)
        if artifact_root is None:
            expected = (
                "protocol.yaml and prediction_generation_status.json. Record product_dir or run_dir"
                if infer_profile
                else "evaluation_payload.json and report.jsonl. Record run_dir or evaluation_run_dir"
            )
            print(
                f"RUN_REPORT_UNIT gate: could not locate a run artifact root with {expected} in main_agent_run_report.json.",
                file=sys.stderr,
            )
            return 1
        artifact_errors = (
            _validate_infer_artifacts(artifact_root) if infer_profile else _validate_completed_artifacts(artifact_root)
        )
        if artifact_errors:
            print("RUN_REPORT_UNIT artifact gate failed:\n  - " + "\n  - ".join(artifact_errors), file=sys.stderr)
            return 1

    print(f"run_report OK: execution_path_actual={execution_path_actual}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
