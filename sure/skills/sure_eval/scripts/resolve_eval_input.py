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
for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure_eval.core.config import Config
from sure_eval.datasets import DatasetManager
from sure_eval.datasets.dataset_manager import CSV_DATASETS
from sure_eval.datasets.source_resolver import (
    SourceResolutionError,
    accepted_source_root,
    is_source_entry,
    read_source_language,
    resolve_aispeech_source_entry,
)

from evaluation_capabilities import default_metrics_for_task_language, supported_metrics_for_task_language
from harness_runtime import HarnessRuntimeBindingError, load_harness_runtime
from resolve_evaluation_engine import resolve_engine_root
from resolve_model_dir import APPROVED_MODELS_ROOT, resolve_approved_model
from sure.site.loader import load_site_policy

from sure_eval.agent import vc_submitter


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
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,199}$")

# Approved models and promoted results both live below the configured trust
# root. Evaluation products never stage inside it; promotion stays human.
_configured_site_policy = load_site_policy()
NFS_ROOT = (
    Path(_configured_site_policy["policy"]["storage"]["forbidden_output_roots"][0])
    if _configured_site_policy
    else Path("<site-policy-required>")
)

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


SYNTH_TASKS = {"TTS", "VC"}
TASK_CHECK_EXEMPT = {"OMNI", "API"}
TASK_WORDS = {"ASR": "speech recognition", "TTS": "speech synthesis", "VC": "voice conversion"}


class EvalInputError(ValueError):
    pass


def _validate_run_id(value: str) -> str:
    if not RUN_ID_RE.fullmatch(value):
        raise EvalInputError(
            "run_id must be one safe path segment (1-200 ASCII letters, digits, '.', '_', '=', or '-')"
        )
    return value


def _resolve_output_dir(value: str | None, staged_dir: Path) -> Path:
    """Resolve where this run's evaluation products go.

    Without `output_dir` products stay in the repository-local staging path.
    An override becomes the product directory itself, which is what a caller
    driving the harness from a script reads afterwards. It must be an absolute
    path outside NFS, because promotion into NFS stays a human step, and it
    must be usable before the state machine starts.
    """
    if not value:
        return staged_dir
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise EvalInputError(
            f"output_dir must be an absolute path, for example "
            f"output_dir={(NFS_ROOT.parent / 'jobs' / 'job-1234').as_posix()} (got {value!r})"
        )
    output_dir = candidate.resolve()
    if output_dir.is_relative_to(NFS_ROOT.resolve()):
        raise EvalInputError(
            f"output_dir must stay outside {NFS_ROOT}: promotion into NFS is a human step"
        )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EvalInputError(f"output_dir cannot be created: {output_dir} ({exc})") from exc
    if not os.access(output_dir, os.W_OK):
        raise EvalInputError(f"output_dir is not writable: {output_dir}")
    return output_dir


def _stage_output_dir(results_root: Path, model: str, protocol: str, run_id: str) -> Path:
    output_dir = (results_root / model / protocol / run_id).resolve()
    try:
        output_dir.relative_to(results_root)
    except ValueError as exc:
        raise EvalInputError(f"evaluation staging path escapes {results_root}: {output_dir}") from exc
    return output_dir


def _task_label(task: str) -> str:
    word = TASK_WORDS.get(task)
    return f"{task} ({word})" if word else task


def _check_task_compatibility(model: dict[str, Any], datasets: list[dict[str, Any]]) -> None:
    model_task = _normalize_task(model.get("declared_task"))
    if not model_task or model_task in TASK_CHECK_EXEMPT:
        return
    model_synth = model_task in SYNTH_TASKS
    mismatched = []
    for item in datasets:
        task = _normalize_task(item.get("task"))
        if task and task != "UNKNOWN" and (task in SYNTH_TASKS) != model_synth:
            mismatched.append(f"dataset '{item.get('name')}' has task {_task_label(task)}")
    if not mismatched:
        return
    if model_synth:
        suggestion = "e.g. the seedtts_test_eval or cv3_eval collections"
    else:
        names = _dedupe([str(item.get("config_name") or "") for item in CSV_DATASETS.values() if _normalize_task(item.get("task")) == model_task])[:4]
        suggestion = f"e.g.: {', '.join(names)}" if names else "check the dataset registry for a compatible dataset"
    raise EvalInputError(
        f"Task mismatch: model '{model.get('name')}' declares task {_task_label(model_task)}, "
        f"but {'; '.join(mismatched)}. Choose datasets that match the model task, {suggestion}. "
        f"(Model task comes from the model's config.yaml; dataset task from dataset metadata.)"
    )


def _check_dataset_input_policy(datasets: list[dict[str, Any]]) -> None:
    """Strict main flow only accepts aispeech source roots (or their outputs)."""
    offenders = []
    for item in datasets:
        requested = str(item.get("requested_name") or item.get("name") or "")
        if is_source_entry(requested) or item.get("source_root"):
            continue
        offenders.append(requested or str(item.get("name")))
    if not offenders:
        return
    root = accepted_source_root()
    raise EvalInputError(
        "Strict main flow accepts only aispeech source-root dataset inputs. "
        f"Rejected: {', '.join(offenders)}. Pass a source root such as "
        f"{root}/g001/store002/ds_pool/<source_dataset_name>, or re-prepare the "
        "dataset from its source root so its JSONL carries source metadata. "
        "Legacy dataset names remain usable for /sure_reval on historical runs, "
        "or pass --no-strict-main-flow explicitly."
    )


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
    env_root = os.environ.get("SURE_EVAL_DATASETS_ROOT", "").strip()
    datasets_root = Path(env_root) if env_root else harness_root / "data" / "datasets"
    if not base_config.exists():
        raise FileNotFoundError(f"Submodule config not found: {base_config}")
    jsonl_dir = datasets_root / "sure_benchmark" / "jsonl"
    if not jsonl_dir.is_dir():
        # An explicit env root must already exist so a mistyped path fails fast;
        # the in-repo default is scaffolding that source prepare fills on demand.
        if env_root:
            raise FileNotFoundError(f"SURE_EVAL_DATASETS_ROOT must contain sure_benchmark/jsonl: {datasets_root}")
        jsonl_dir.mkdir(parents=True, exist_ok=True)

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


def _normalize_execution(
    execution: str | None,
    execution_path: str | None,
    allowed_surfaces: list[str] | None = None,
) -> dict[str, Any]:
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
        planned_path = "local_docker" if path_raw == "local_bash" else path_raw
    elif requested == "vc":
        planned_path = "vc_submit"
    elif requested == "local":
        planned_path = "local_docker"
    else:
        planned_path = "auto"
    if planned_path not in {"auto", "vc_submit", "local_bash", "local_docker"}:
        raise ValueError(f"Unsupported execution_path: {execution_path}. Use auto, vc_submit, local_bash, or local_docker.")

    available = _vc_available()
    if planned_path == "auto":
        allowed = set(allowed_surfaces or ("local", "vc"))
        if available and "vc" in allowed:
            planned_path = "vc_submit"
        elif "local" in allowed:
            planned_path = "local_docker"
        elif "vc" in allowed:
            planned_path = "vc_submit"
        else:
            raise ValueError("site policy enables no supported execution surface")
    planned = "vc" if planned_path == "vc_submit" else "local"
    if allowed_surfaces is not None and planned not in set(allowed_surfaces):
        raise ValueError(f'execution surface "{planned}" is not enabled by the active site policy')
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


_VC_INFO_TIMEOUT_SECONDS = 30


def _validate_vc_partition(vc_request: dict[str, Any], execution: dict[str, Any]) -> None:
    """Fail fast when an explicit vc_partition is not in the user's allowed set.

    Skips silently when the run is not planned for vc, when ``vc info -u``
    fails or times out, or when the parsed partition list is empty: the later
    ``vc submit`` stays authoritative, this check only shortens the feedback
    loop for typos.
    """
    partition = str(vc_request.get("partition") or "").strip()
    if not partition or execution.get("planned") != "vc":
        return
    try:
        allowed = vc_submitter.get_user_partitions(timeout=_VC_INFO_TIMEOUT_SECONDS)
    except Exception:
        return
    if not allowed:
        return
    if partition not in allowed:
        raise EvalInputError(
            f'vc_partition "{partition}" is not in your allowed partitions. '
            f"Allowed: {', '.join(sorted(allowed))}"
        )


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
        sample_meta = first_sample.get("metadata") if isinstance(first_sample.get("metadata"), dict) else {}
        source_root = info.get("source_root") or sample_meta.get("source_dataset_root")
        source_name = info.get("source_dataset_name") or sample_meta.get("source_dataset_name")
        version_id = info.get("version_id") or sample_meta.get("version_id")
        dataset_task = _normalize_task(info.get("task") or first_sample.get("task") or "")
        language = str(info.get("language") or first_sample.get("language") or "").lower()
        if is_source_entry(requested_name) and not jsonl_path.exists():
            ref = resolve_aispeech_source_entry(requested_name)
            source_root = source_root or ref.source_root
            source_name = source_name or ref.source_dataset_name
            version_id = version_id or ref.version_id
            dataset_task = dataset_task or "ASR"
            language = language or (read_source_language(ref) or "auto").lower()
        task = _effective_dataset_task(dataset_task, model_task, requested_metrics)
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
        if source_root:
            detail["source_root"] = str(source_root)
        if source_name:
            detail["source_dataset_name"] = str(source_name)
        if version_id:
            detail["version_id"] = str(version_id)
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
            sample_meta = first_sample.get("metadata") if isinstance(first_sample.get("metadata"), dict) else {}
            if sample_meta.get("source_dataset_root"):
                detail["source_root"] = str(sample_meta["source_dataset_root"])
            if sample_meta.get("source_dataset_name"):
                detail["source_dataset_name"] = str(sample_meta["source_dataset_name"])
            if sample_meta.get("version_id"):
                detail["version_id"] = str(sample_meta["version_id"])
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


def _resolve_model(model: str) -> dict[str, Any]:
    resolution = resolve_approved_model(model)
    model_dir = Path(resolution["model_dir"]) if resolution.get("model_dir") else None
    checks = resolution["checks"]
    runtime_ready = bool(resolution["runtime_ready"])
    verdict_ready = bool(resolution["verdict_ready"])
    declared_task = _read_model_task(model_dir)
    return {
        "name": model,
        "model_dir": str(model_dir) if model_dir else None,
        "declared_task": declared_task,
        "source": resolution["source"],
        "approved_models_root": resolution["approved_models_root"],
        "verdict_path": resolution["verdict_path"],
        "workflow_ready": runtime_ready and verdict_ready,
        "integration_state": "onboarded" if runtime_ready and verdict_ready else "needs_onboarding",
        "checks": checks,
        "deployment_binding": resolution.get("deployment_binding"),
        "deployment_error": resolution.get("deployment_error"),
        "evidence": {
            "readme_path": str(model_dir / "README.md") if model_dir else "",
            "config_path": str(model_dir / "config.yaml") if model_dir else "",
            "artifacts_dir": str(model_dir / "artifacts") if model_dir else "",
            "model_spec_path": str(model_dir / "model.spec.yaml") if model_dir else "",
            "server_path": str(model_dir / "server.py") if model_dir else "",
            "model_py_path": str(model_dir / "model.py") if model_dir else "",
        },
        "candidates": [
            {
                "source": resolution["source"],
                "path": str(Path(resolution["approved_models_root"]) / model),
            }
        ],
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = _repo_root_from_script()
    try:
        active_site_policy = load_site_policy(required=True)
    except ValueError as exc:
        raise EvalInputError(str(exc)) from exc
    try:
        harness_runtime = load_harness_runtime()
    except HarnessRuntimeBindingError as exc:
        raise EvalInputError(str(exc)) from exc
    protocol = str(getattr(args, "protocol", None) or "standard_system")
    run_id = _validate_run_id(
        args.run_id or f"main_agent_{args.model}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    )
    model = _resolve_model(args.model)
    if not model.get("workflow_ready"):
        raise EvalInputError(
            f"model {args.model!r} is not an approved runtime-ready NFS model under "
            f"{model.get('approved_models_root')}"
        )
    model_dir = Path(str(model["model_dir"]))
    image_harness = ((model.get("deployment_binding") or {}).get("container") or {}).get("harness_runtime")
    if isinstance(image_harness, dict):
        if image_harness.get("runtime_id") != harness_runtime.get("runtime_id"):
            raise EvalInputError("approved image Harness Runtime ID differs from the active Harness Runtime")
        if image_harness.get("lock_sha256") != harness_runtime.get("lock_sha256"):
            raise EvalInputError("approved image Harness Runtime lock differs from the active Harness Runtime")
    results_root = (repo_root / "sure" / "results").resolve()
    output_dir = _resolve_output_dir(
        getattr(args, "output_dir", ""),
        _stage_output_dir(results_root, args.model, protocol, run_id),
    )
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
    if args.strict_main_flow:
        _check_task_compatibility(model, datasets)
        _check_dataset_input_policy(datasets)
    tasks = _dedupe([str(item.get("task") or "UNKNOWN") for item in datasets])
    languages = _dedupe([str(item.get("language") or "") for item in datasets if item.get("language")])
    metric_list = _dedupe([metric for item in datasets for metric in item.get("default_metrics", [])])
    allowed_surfaces = list(active_site_policy["policy"]["execution"]["surfaces"])
    execution = _normalize_execution(args.execution, args.execution_path, allowed_surfaces)
    device = _resolve_device(args.device, execution)
    vc_request = _vc_request(args)
    approved_image = str((model.get("deployment_binding") or {}).get("target_image_ref") or "")
    if vc_request.get("image") and vc_request["image"] != approved_image:
        raise EvalInputError(
            f"vc_image cannot override the approved digest-pinned image {approved_image}"
        )
    vc_request["image"] = approved_image
    _validate_vc_partition(vc_request, execution)

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
            "deployment_binding": model["deployment_binding"],
            "harness_runtime": harness_runtime,
        },
    }

    return {
        "schema": "sure.eval.input_resolved.v1",
        "generated_at": _utc_now(),
        "user_input": {
            "model": args.model,
            "datasets": requested_datasets,
            "protocol": protocol,
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
            "protocol_id": protocol,
            "max_samples": args.max_samples,
            "sample_scope": "full_dataset" if args.max_samples == 0 else "bounded",
            "device": device,
            "execution": execution,
            "execution_path": execution["path_planned"],
            "vc": vc_request,
            "deployment_binding": model["deployment_binding"],
            "harness_runtime": harness_runtime,
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve /sure_eval input into a main-flow contract")
    parser.add_argument("--model", required=True)
    parser.add_argument("--datasets", nargs="+", required=True)
    parser.add_argument("--protocol", choices=("standard_system", "strict_core"), default="standard_system")
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
    parser.add_argument("--run-id")
    parser.add_argument("--config")
    parser.add_argument("--output")
    parser.add_argument("--output-dir", default="")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    try:
        payload = build_payload(args)
    except (EvalInputError, SourceResolutionError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
