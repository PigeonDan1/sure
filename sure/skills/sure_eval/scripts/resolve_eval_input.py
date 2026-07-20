#!/usr/bin/env python3
"""Resolve the user-facing /sure_eval input into a main-flow input contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.config import Config
from sure_eval.datasets import DatasetManager

from evaluation_capabilities import default_metrics_for_task_language, supported_metrics_for_task_language
from resolve_evaluation_engine import resolve_engine_root
from resolve_model_dir import _candidate_model_dirs, _checks, _first_existing, _verdict_path


MAIN_FLOW_SCRIPTS = [
    "scripts/prepare_sure_dataset.py",
    "scripts/materialize_predictions_template.py",
    "scripts/generate_predictions_via_server.py",
    "scripts/validate_prediction_files.py",
    "scripts/evaluate_predictions.py",
    "scripts/refresh_report_snapshot.py",
    "scripts/run_local_execution.py",
    "scripts/run_vc_execution.py",
]

TEXT_DEFAULT_METRICS = {
    "S2TT": "bleu",
    "SD": "der",
    "SA-ASR": "cpwer",
    "KWS": "accuracy",
    "SER": "accuracy",
    "GR": "accuracy",
    "SLU": "accuracy",
    "SPEECH_UNDERSTANDING": "accuracy",
}

def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[4]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_values(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for item in str(value).replace(",", " ").split():
            item = item.strip()
            if item and item not in out:
                out.append(item)
    return out


def _normalize_task(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_")


def _metric_task_hint(metrics: list[str]) -> str:
    hinted: list[str] = []
    for metric in metrics:
        metric_name = str(metric or "").strip().lower()
        if metric_name.startswith("vc_"):
            hinted.append("VC")
        elif metric_name.startswith("tts_"):
            hinted.append("TTS")
    hinted = _dedupe(hinted)
    return hinted[0] if len(hinted) == 1 else ""


def _read_model_task(model_dir: Path | None) -> str:
    if model_dir is None:
        return ""
    config_path = model_dir / "config.yaml"
    if not config_path.exists():
        return ""
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    model_section = config.get("model") if isinstance(config.get("model"), dict) else {}
    return _normalize_task(model_section.get("task") or config.get("task") or config.get("task_type"))


def _effective_dataset_task(dataset_task: str, model_task: str, metrics: list[str]) -> str:
    task = _normalize_task(dataset_task) or "UNKNOWN"
    model_task = _normalize_task(model_task)
    metric_task = _metric_task_hint(metrics)
    if task in {"TTS", "VC"}:
        if metric_task in {"TTS", "VC"}:
            return metric_task
        if model_task in {"TTS", "VC"}:
            return model_task
    return task


def _write_harness_config(*, run_dir: Path, config_path: str | None) -> Path:
    if config_path:
        path = Path(config_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        return path.resolve()

    env_config = os.environ.get("SURE_EVAL_CONFIG")
    if env_config:
        path = Path(env_config).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"SURE_EVAL_CONFIG file not found: {path}")
        return path.resolve()

    harness_root = _repo_root_from_script()
    base_config = harness_root / "sure" / "external" / "sure-evaluation" / "config" / "default.yaml"
    datasets_root = Path(os.environ.get("SURE_EVAL_DATASETS_ROOT", harness_root / "data" / "datasets"))
    if not base_config.exists():
        raise FileNotFoundError(f"Submodule config not found: {base_config}")
    if not (datasets_root / "sure_benchmark" / "jsonl").is_dir():
        raise FileNotFoundError(f"SURE_EVAL_DATASETS_ROOT must contain sure_benchmark/jsonl: {datasets_root}")

    config = yaml.safe_load(base_config.read_text(encoding="utf-8")) or {}
    data = dict(config.get("data") or {})
    data.update(
        {
            "root": str(harness_root / "data"),
            "cache": str(harness_root / "data" / "cache"),
            "models": str(harness_root / "data" / "models"),
            "datasets": str(datasets_root),
            "results": str(run_dir / "results"),
        }
    )
    config["data"] = data
    output = run_dir / "_harness_config.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return output.resolve()


def _nvidia_smi_available() -> bool:
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        return False
    if not shutil.which("nvidia-smi"):
        return False
    completed = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, check=False, timeout=10)
    return completed.returncode == 0 and bool(completed.stdout.strip())


def _resolve_device(requested: str, execution: dict[str, Any] | None = None) -> dict[str, Any]:
    request = (requested or "auto").strip()
    lowered = request.lower()
    cuda_available = _nvidia_smi_available()
    execution = execution or {}
    planned_path = str(execution.get("path_planned") or "")
    planned_execution = str(execution.get("planned") or "")
    vc_planned = planned_path == "vc_submit" or planned_execution == "vc"
    notes: list[str] = []
    source = "local_nvidia_smi"

    if vc_planned:
        source = "vc_allocation"
        cuda_available = True
        cuda_index = re.fullmatch(r"cuda:(\d+)", lowered)
        if lowered == "auto":
            resolved = "cuda:0"
            notes.append("execution=vc uses the GPU allocated inside the vc container; host nvidia-smi is not used.")
        elif lowered == "cuda":
            resolved = "cuda:0"
        elif cuda_index:
            resolved = "cuda:0"
            if cuda_index.group(1) != "0":
                notes.append(
                    f"device={request} names a host-local CUDA ordinal; vc containers expose allocated GPUs from cuda:0."
                )
        elif lowered == "cpu":
            resolved = "cpu"
            notes.append("execution=vc will still submit to vc, but model inference is explicitly requested on CPU.")
        else:
            raise ValueError(f"Unsupported device: {requested}. Use auto, cpu, cuda, or cuda:<index>.")
    elif lowered == "auto":
        resolved = "cuda:0" if cuda_available else "cpu"
    elif lowered == "cuda":
        resolved = "cuda:0"
    elif lowered.startswith("cuda") or lowered == "cpu":
        resolved = request
    else:
        raise ValueError(f"Unsupported device: {requested}. Use auto, cpu, cuda, or cuda:<index>.")
    return {
        "request": request,
        "resolved": resolved,
        "cuda_available": cuda_available,
        "cpu_forces_cuda_hidden": resolved.lower() == "cpu",
        "execution_device_source": source,
        "notes": notes,
    }


def _vc_available() -> bool:
    if not shutil.which("vc"):
        return False
    try:
        completed = subprocess.run(["vc", "info"], capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _normalize_execution(execution: str | None, execution_path: str | None) -> dict[str, Any]:
    requested_raw = (execution or "").strip().lower()
    path_raw = (execution_path or "").strip().lower()
    path_to_execution = {
        "vc_submit": "vc",
        "local_bash": "local",
        "local_docker": "local",
        "auto": "auto",
    }
    if not requested_raw:
        requested_raw = path_to_execution.get(path_raw, "auto")
    aliases = {
        "vc_submit": "vc",
        "vc": "vc",
        "volcano": "vc",
        "local": "local",
        "local_bash": "local",
        "local_docker": "local",
        "bash": "local",
        "auto": "auto",
    }
    requested = aliases.get(requested_raw)
    if requested is None:
        raise ValueError(f"Unsupported execution: {execution}. Use auto, local, or vc.")
    if execution and path_raw and path_raw != "auto":
        path_requested = aliases.get(path_raw)
        if path_requested and path_requested != requested:
            raise ValueError(
                f"Conflicting execution parameters: execution={execution} but execution_path={execution_path}. "
                "Use execution=auto|local|vc, or the matching legacy execution_path."
            )

    if path_raw and path_raw != "auto":
        planned_path = path_raw
    elif requested == "vc":
        planned_path = "vc_submit"
    elif requested == "local":
        planned_path = "local_bash"
    else:
        planned_path = "auto"
    if planned_path not in {"auto", "vc_submit", "local_bash", "local_docker"}:
        raise ValueError(f"Unsupported execution_path: {execution_path}. Use auto, vc_submit, local_bash, or local_docker.")

    available = _vc_available()
    if planned_path == "auto":
        planned_path = "vc_submit" if available else "local_bash"
    planned = "vc" if planned_path == "vc_submit" else "local"
    reason = (
        "user_requested_vc"
        if requested == "vc"
        else "user_requested_local"
        if requested == "local"
        else "auto_selected_vc_available"
        if planned == "vc"
        else "auto_selected_local_vc_unavailable"
    )
    return {
        "requested": requested,
        "planned": planned,
        "path_requested": path_raw or "auto",
        "path_planned": planned_path,
        "vc_available_at_resolve": available,
        "fallback_allowed": requested == "auto",
        "reason": reason,
    }


def _vc_request(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "partition": args.vc_partition,
        "cpu": args.vc_cpu,
        "mem": args.vc_mem,
        "gpu": args.vc_gpu,
        "image": args.vc_image,
        "job_name": args.vc_job_name,
    }


def _count_jsonl_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _first_jsonl_sample(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                return json.loads(line)
    return {}


def _fallback_default_metrics(task: str, language: str) -> list[str]:
    task_upper = task.upper()
    language_lower = language.lower()
    if task_upper == "ASR":
        if language_lower == "cs":
            return ["mer"]
        return ["wer"] if language_lower == "en" else ["cer"]
    if task_upper in {"TTS", "VC"}:
        return []
    return [TEXT_DEFAULT_METRICS.get(task_upper, "accuracy")]


def _default_metrics(task: str, language: str, engine_root: Path | None) -> list[str]:
    task_upper = task.upper()
    if engine_root is not None:
        try:
            if task_upper in {"TTS", "VC"}:
                metrics = supported_metrics_for_task_language(engine_root, task, language)
            else:
                metrics = default_metrics_for_task_language(engine_root, task, language)
            metrics = [metric for metric in metrics if metric != "multi"]
            if metrics:
                return metrics
        except Exception:
            pass
    return _fallback_default_metrics(task, language)


def _dataset_details(
    manager: DatasetManager,
    names: list[str],
    requested_metrics: list[str],
    engine_root: Path | None,
    model_task: str = "",
) -> list[dict[str, Any]]:
    expanded = manager.expand_dataset_names(names)
    details: list[dict[str, Any]] = []
    seen: set[str] = set()
    for requested_name, dataset_name in [(name, item) for name in names for item in manager.expand_dataset_names([name])]:
        if dataset_name in seen:
            continue
        seen.add(dataset_name)
        info = manager.get_info(dataset_name) or {}
        jsonl_path = Path(str(info.get("jsonl_path") or manager.get_jsonl_path(dataset_name)))
        first_sample = _first_jsonl_sample(jsonl_path)
        dataset_task = _normalize_task(info.get("task") or first_sample.get("task") or "")
        task = _effective_dataset_task(dataset_task, model_task, requested_metrics)
        language = str(info.get("language") or first_sample.get("language") or "").lower()
        if not task:
            task = "UNKNOWN"
        metrics = requested_metrics or _default_metrics(task, language, engine_root)
        detail = {
            "name": dataset_name,
            "requested_name": requested_name,
            "jsonl_path": str(jsonl_path),
            "jsonl_exists": jsonl_path.exists(),
            "task": task,
            "language": language,
            "default_metrics": metrics,
            "source": info.get("source"),
            "num_samples": info.get("num_samples") or _count_jsonl_rows(jsonl_path),
            "display_name": info.get("display_name") or dataset_name,
        }
        if dataset_task and dataset_task != task:
            detail["dataset_task"] = dataset_task
            detail["task_source"] = "model_or_metric_intent"
        details.append(detail)
    if len(details) != len(expanded):
        seen = {item["name"] for item in details}
        for dataset_name in expanded:
            if dataset_name in seen:
                continue
            info = manager.get_info(dataset_name) or {}
            jsonl_path = Path(str(info.get("jsonl_path") or manager.get_jsonl_path(dataset_name)))
            first_sample = _first_jsonl_sample(jsonl_path)
            dataset_task = _normalize_task(info.get("task") or first_sample.get("task") or "")
            task = _effective_dataset_task(dataset_task, model_task, requested_metrics)
            language = str(info.get("language") or first_sample.get("language") or "").lower()
            detail = {
                "name": dataset_name,
                "requested_name": dataset_name,
                "jsonl_path": str(jsonl_path),
                "jsonl_exists": jsonl_path.exists(),
                "task": task,
                "language": language,
                "default_metrics": requested_metrics or _default_metrics(task, language, engine_root),
                "source": info.get("source"),
                "num_samples": info.get("num_samples") or _count_jsonl_rows(jsonl_path),
                "display_name": info.get("display_name") or dataset_name,
            }
            if dataset_task and dataset_task != task:
                detail["dataset_task"] = dataset_task
                detail["task_source"] = "model_or_metric_intent"
            details.append(detail)
    return details


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _resolve_model(model: str, explicit_model_dir: str | None, cwd: Path) -> dict[str, Any]:
    candidates = _candidate_model_dirs(model, explicit_model_dir, cwd)
    selected = _first_existing(candidates)
    source = selected[0] if selected else None
    model_dir = selected[1].resolve() if selected else None
    verdict = _verdict_path(model_dir) if model_dir else None
    checks = _checks(model_dir)
    runtime_ready = bool(checks["config_yaml"] and checks["model_py"] and checks["server_py"])
    verdict_ready = verdict is not None
    declared_task = _read_model_task(model_dir)
    return {
        "name": model,
        "model_dir": str(model_dir) if model_dir else None,
        "declared_task": declared_task,
        "source": source,
        "verdict_path": str(verdict.resolve()) if verdict else None,
        "workflow_ready": runtime_ready and verdict_ready,
        "integration_state": "onboarded" if runtime_ready and verdict_ready else "needs_onboarding",
        "checks": checks,
        "evidence": {
            "readme_path": str(model_dir / "README.md") if model_dir else "",
            "config_path": str(model_dir / "config.yaml") if model_dir else "",
            "artifacts_dir": str(model_dir / "artifacts") if model_dir else "",
            "model_spec_path": str(model_dir / "model.spec.yaml") if model_dir else "",
            "server_path": str(model_dir / "server.py") if model_dir else "",
            "model_py_path": str(model_dir / "model.py") if model_dir else "",
        },
        "candidates": [{"source": item_source, "path": str(path)} for item_source, path in candidates],
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = _repo_root_from_script()
    cwd = Path(args.cwd).expanduser().resolve()
    run_id = args.run_id or f"main_agent_{args.model}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    model = _resolve_model(args.model, args.model_dir, cwd)
    model_dir = Path(model["model_dir"]) if model.get("model_dir") else repo_root / "sure" / "models" / args.model
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else model_dir / "eval_runs" / run_id
    config_path = _write_harness_config(run_dir=output_dir, config_path=args.config)
    cfg = Config.from_yaml(config_path)
    manager = DatasetManager(cfg)
    requested_datasets = _split_values(args.datasets)
    requested_metrics = _split_values(args.metrics)
    engine = resolve_engine_root(args.evaluation_engine_root)
    engine_root = engine[1] if engine is not None else None
    datasets = _dataset_details(
        manager,
        requested_datasets,
        requested_metrics,
        engine_root,
        model_task=str(model.get("declared_task") or ""),
    )
    tasks = _dedupe([str(item.get("task") or "UNKNOWN") for item in datasets])
    languages = _dedupe([str(item.get("language") or "") for item in datasets if item.get("language")])
    metric_list = _dedupe([metric for item in datasets for metric in item.get("default_metrics", [])])
    execution = _normalize_execution(args.execution, args.execution_path)
    device = _resolve_device(args.device, execution)
    vc_request = _vc_request(args)

    main_flow_input = {
        "user_goal": args.user_goal,
        "target": {
            "model_name": args.model,
            "model_dir": str(model_dir),
            "tool_workflow_ready": bool(model.get("workflow_ready")),
            "integration_state": model.get("integration_state"),
        },
        "constraints": {
            "allow_tool_workflow": args.allow_tool_workflow,
            "allowed_tasks": tasks,
            "allowed_datasets": [item["name"] for item in datasets],
            "blocked_datasets": [],
            "dry_run": args.dry_run,
        },
        "evidence": {
            "readme_path": model["evidence"]["readme_path"],
            "config_path": model["evidence"]["config_path"],
            "artifacts_dir": model["evidence"]["artifacts_dir"],
            "model_spec_path": model["evidence"]["model_spec_path"],
            "prior_results": [],
        },
        "runtime_context": {
            "available_scripts": MAIN_FLOW_SCRIPTS,
            "output_dir": str(output_dir),
            "device_request": device["request"],
            "device_resolved": device["resolved"],
            "execution": execution,
            "execution_path": execution["path_planned"],
            "max_samples": args.max_samples,
        },
    }

    return {
        "schema": "sure.eval.input_resolved.v1",
        "generated_at": _utc_now(),
        "user_input": {
            "model": args.model,
            "datasets": requested_datasets,
            "device": args.device,
            "metrics": requested_metrics,
            "max_samples": args.max_samples,
            "execution": execution["requested"],
            "execution_path": args.execution_path or "auto",
            "user_goal": args.user_goal,
            "vc": vc_request,
        },
        "model": model,
        "datasets": datasets,
        "task_summary": {
            "evaluation_tasks": tasks,
            "languages": languages,
            "single_task": len([task for task in tasks if task != "UNKNOWN"]) <= 1,
            "audio_evaluation_required": any(task in {"TTS", "VC"} for task in tasks),
            "metrics": metric_list,
        },
        "runtime": {
            "run_id": run_id,
            "run_dir": str(output_dir),
            "max_samples": args.max_samples,
            "sample_scope": "full_dataset" if args.max_samples == 0 else "bounded",
            "device": device,
            "execution": execution,
            "execution_path": execution["path_planned"],
            "vc": vc_request,
        },
        "evaluation": {
            "backend": args.evaluation_backend,
            "strict_main_flow": args.strict_main_flow,
            "config_path": str(config_path),
            "engine": (
                {"source": engine[0], "engine_root": str(engine[1])}
                if engine is not None
                else None
            ),
        },
        "main_flow_input": main_flow_input,
        "expected_outputs": {
            "protocol": str(output_dir / "protocol.yaml"),
            "report_jsonl": str(output_dir / "report.jsonl"),
            "evaluation_payload": str(output_dir / "evaluation_payload.json"),
            "sample_reports_dir": str(output_dir / "sample_reports"),
            "metrics_dir": str(output_dir / "metrics"),
            "predictions_dir": str(output_dir / "predictions"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve /sure_eval input into a main-flow contract")
    parser.add_argument("--model", required=True)
    parser.add_argument("--datasets", nargs="+", required=True)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--metrics", nargs="*", default=[])
    parser.add_argument("--max-samples", type=int, default=0)
    parser.add_argument("--execution", choices=("auto", "local", "vc"))
    parser.add_argument("--execution-path", default="auto", choices=("local_bash", "local_docker", "vc_submit", "auto"))
    parser.add_argument("--vc-partition", default="")
    parser.add_argument("--vc-cpu", type=int, default=0)
    parser.add_argument("--vc-mem", default="")
    parser.add_argument("--vc-gpu", type=int, default=0)
    parser.add_argument("--vc-image", default="")
    parser.add_argument("--vc-job-name", default="")
    parser.add_argument("--evaluation-backend", default="external", choices=("auto", "external", "legacy"))
    parser.add_argument("--evaluation-engine-root")
    parser.add_argument("--strict-main-flow", action="store_true", default=True)
    parser.add_argument("--no-strict-main-flow", dest="strict_main_flow", action="store_false")
    parser.add_argument("--user-goal", default="evaluate_existing_model")
    parser.add_argument("--allow-tool-workflow", action="store_true", default=True)
    parser.add_argument("--no-allow-tool-workflow", dest="allow_tool_workflow", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model-dir")
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir")
    parser.add_argument("--config")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--output")
    args = parser.parse_args()

    payload = build_payload(args)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
