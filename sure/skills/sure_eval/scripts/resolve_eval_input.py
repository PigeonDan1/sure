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
    resolve_site_source_entry,
)

from evaluation_capabilities import default_metrics_for_task_language, supported_metrics_for_task_language
from harness_runtime import HarnessRuntimeBindingError, load_harness_runtime
from resolve_evaluation_engine import resolve_engine_root
from resolve_model_dir import APPROVED_MODELS_ROOT, resolve_approved_model
from sure.site.loader import load_site_policy


MAIN_FLOW_SCRIPTS = [
    "scripts/prepare_sure_dataset.py",
    "scripts/materialize_predictions_template.py",
    "scripts/generate_predictions_via_server.py",
    "scripts/validate_prediction_files.py",
    "scripts/evaluate_predictions.py",
    "scripts/refresh_report_snapshot.py",
    "scripts/run_local_execution.py",
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
    """Strict main flow only accepts site dataset source roots (or their outputs)."""
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
        "Strict main flow accepts only site dataset source-root inputs. "
        f"Rejected: {', '.join(offenders)}. Pass a source root such as "
        f"{root}/g001/store002/ds_pool/<source_dataset_name>, or re-prepare the "
        "dataset from its source root so its JSONL carries source metadata. "
        "Legacy dataset names remain usable for /sure_reval on historical runs, "
        "or pass --no-strict-main-flow explicitly."
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _select_harness_config(config_path: str | None, harness_root: Path) -> tuple[Path, str]:
    if config_path:
        path = Path(config_path).expanduser()
        label = "--config"
    else:
        env_config = os.environ.get("SURE_EVAL_CONFIG", "").strip()
        if env_config:
            path = Path(env_config).expanduser()
            label = "SURE_EVAL_CONFIG"
        else:
            path = harness_root / "sure" / "external" / "sure-evaluation" / "config" / "default.yaml"
            label = "submodule config"
    if not path.is_file():
        raise FileNotFoundError(f"{label} file not found: {path}")
    return path.resolve(), label


def _projection_root_hint(source: str) -> str:
    """Where the unusable root came from and how to get past it.

    The bundled policy once shipped the example value /var/lib/sure/..., which no
    ordinary user can create, and the message named neither the policy nor an
    override: every run on that checkout died in pre_start with a bare Errno 13.
    """
    if source == "site_policy":
        return (
            "datasets.projection_root in the active site policy points somewhere this user "
            "cannot write. Fix the policy, or override it with --datasets-root or "
            "SURE_EVAL_DATASETS_ROOT."
        )
    return "Override it with --datasets-root or SURE_EVAL_DATASETS_ROOT."


def _resolve_dataset_projection(
    *,
    explicit_root: str | None,
    configured_root: object,
    harness_root: Path,
    site_policy: dict[str, Any],
    reserved_write_roots: tuple[Path, ...] = (),
) -> dict[str, str]:
    env_root = os.environ.get("SURE_EVAL_DATASETS_ROOT", "").strip()
    policy = site_policy["policy"]
    policy_root = str((policy.get("datasets") or {}).get("projection_root") or "").strip()
    config_root = str(configured_root or "").strip()
    candidates = (
        (str(explicit_root or "").strip(), "command"),
        (env_root, "environment"),
        (policy_root, "site_policy"),
        (config_root, "config"),
        (str(harness_root / "data" / "datasets"), "development_default"),
    )
    raw_root, source = next((value, origin) for value, origin in candidates if value)
    candidate = Path(raw_root).expanduser()
    if not candidate.is_absolute():
        raise EvalInputError(f"dataset projection root from {source} must be absolute: {candidate}")
    projection_root = candidate.resolve()

    for raw_forbidden in policy["storage"]["forbidden_output_roots"]:
        forbidden = Path(str(raw_forbidden)).resolve()
        if _is_within(projection_root, forbidden):
            raise EvalInputError(
                f"dataset projection root is under a forbidden output root: {projection_root}"
            )
    for raw_source in policy["datasets"]["allowed_source_roots"].values():  # key -> path since 19b17fc
        source_root = Path(str(raw_source)).resolve()
        if _is_within(projection_root, source_root) or _is_within(source_root, projection_root):
            raise EvalInputError(
                "dataset projection root must not overlap an allowed source root: "
                f"{projection_root} vs {source_root}"
            )
    for reserved_root in reserved_write_roots:
        reserved = reserved_root.resolve()
        if _is_within(projection_root, reserved) or _is_within(reserved, projection_root):
            raise EvalInputError(
                "dataset projection root must not overlap the evaluation output directory: "
                f"{projection_root} vs {reserved}"
            )

    jsonl_root = projection_root / "sure_benchmark" / "jsonl"
    try:
        jsonl_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EvalInputError(
            f"cannot create dataset projection root {projection_root} (from {source}): {exc}. "
            f"{_projection_root_hint(source)}"
        ) from exc
    if not os.access(projection_root, os.W_OK) or not os.access(jsonl_root, os.W_OK):
        raise EvalInputError(
            f"dataset projection root is not writable: {projection_root} (from {source}). "
            f"{_projection_root_hint(source)}"
        )
    return {
        "host_root": str(projection_root),
        "jsonl_root": str(jsonl_root),
        "source": source,
        "content": "generated_jsonl_indexes_and_metadata",
        "raw_data_policy": "reference_only_no_copy_or_move",
    }


def _materialize_harness_config(
    *,
    run_dir: Path,
    config_path: str | None,
    datasets_root: str | None = None,
    site_policy: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, str]]:
    harness_root = _repo_root_from_script()
    base_config, config_source = _select_harness_config(config_path, harness_root)
    config = yaml.safe_load(base_config.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise EvalInputError(f"evaluation config must be a mapping: {base_config}")
    data = dict(config.get("data") or {})
    active_site_policy = site_policy or load_site_policy(repository_root=harness_root, required=True)
    projection = _resolve_dataset_projection(
        explicit_root=datasets_root,
        configured_root=data.get("datasets") if config_source != "submodule config" else None,
        harness_root=harness_root,
        site_policy=active_site_policy,
        reserved_write_roots=(run_dir,),
    )
    if config_source == "submodule config":
        data.update({
            "root": str(harness_root / "data"),
            "cache": str(harness_root / "data" / "cache"),
            "models": str(harness_root / "data" / "models"),
            "results": str(run_dir / "results"),
        })
    data["datasets"] = projection["host_root"]
    config["data"] = data
    output = run_dir / "_harness_config.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return output.resolve(), projection


def _write_harness_config(*, run_dir: Path, config_path: str | None) -> Path:
    """Compatibility helper preserving the historical config selection contract."""
    harness_root = _repo_root_from_script()
    selected, source = _select_harness_config(config_path, harness_root)
    if source != "submodule config":
        return selected
    output, _ = _materialize_harness_config(
        run_dir=run_dir,
        config_path=None,
    )
    return output


def _nvidia_smi_available() -> bool:
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        return False
    if not shutil.which("nvidia-smi"):
        return False
    completed = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, check=False, timeout=10)
    return completed.returncode == 0 and bool(completed.stdout.strip())


def _resolve_device(requested: str) -> dict[str, Any]:
    request = (requested or "auto").strip()
    lowered = request.lower()
    cuda_available = _nvidia_smi_available()

    if lowered == "auto":
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
        "execution_device_source": "local_nvidia_smi",
        "notes": [],
    }


def _normalize_execution(
    execution: str | None,
    execution_path: str | None,
    runtime_kind: str = "container",
    allowed_local_runtimes: list[str] | None = None,
) -> dict[str, Any]:
    requested_raw = (execution or "").strip().lower()
    path_raw = (execution_path or "").strip().lower()
    if requested_raw in {"vc", "volcano", "vc_submit"} or path_raw == "vc_submit":
        raise ValueError("execution=vc is no longer supported")
    path_to_execution = {
        "local_bash": "local",
        "local_docker": "local",
        "local_python": "local",
        "auto": "auto",
    }
    if not requested_raw:
        requested_raw = path_to_execution.get(path_raw, "auto")
    aliases = {
        "local": "local",
        "local_bash": "local",
        "local_docker": "local",
        "local_python": "local",
        "bash": "local",
        "auto": "auto",
    }
    requested = aliases.get(requested_raw)
    if requested is None:
        raise ValueError(f"Unsupported execution: {execution}. Use auto or local.")
    if execution and path_raw and path_raw != "auto":
        path_requested = aliases.get(path_raw)
        if path_requested and path_requested != requested:
            raise ValueError(
                f"Conflicting execution parameters: execution={execution} but execution_path={execution_path}. "
                "Use execution=auto|local, or the matching legacy execution_path."
            )

    local_path = "local_python" if runtime_kind == "python" else "local_docker"
    if path_raw and path_raw != "auto":
        planned_path = local_path if path_raw == "local_bash" else path_raw
        if planned_path.startswith("local_") and planned_path != local_path:
            raise ValueError(
                f"execution_path={planned_path} conflicts with approved runtime={runtime_kind}; expected {local_path}"
            )
    else:
        planned_path = local_path
    if planned_path not in {"local_docker", "local_python"}:
        raise ValueError(
            f"Unsupported execution_path: {execution_path}. Use auto, local_python, or local_docker."
        )

    if runtime_kind not in set(allowed_local_runtimes or ("container",)):
        raise ValueError(f'local runtime "{runtime_kind}" is not enabled by execution.local_runtimes')
    reason = (
        "user_requested_local"
        if requested == "local"
        else "auto_selected_local_runtime_only"
        if runtime_kind == "python"
        else "auto_selected_local"
    )
    return {
        "requested": requested,
        "planned": "local",
        "path_requested": path_raw or "auto",
        "path_planned": planned_path,
        "fallback_allowed": requested == "auto",
        "reason": reason,
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
    dataset_source_key: str = "default",
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
            ref = resolve_site_source_entry(requested_name, dataset_source_key=dataset_source_key)
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
    deployment_binding = model.get("deployment_binding") or {}
    runtime_kind = str(deployment_binding.get("runtime_kind") or "container")
    image_harness = ((deployment_binding.get("container") or {})).get("harness_runtime")
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
    config_path, dataset_projection = _materialize_harness_config(
        run_dir=output_dir,
        config_path=args.config,
        datasets_root=getattr(args, "datasets_root", None),
        site_policy=active_site_policy,
    )
    cfg = Config.from_yaml(config_path)
    manager = DatasetManager(cfg, dataset_source_key=args.dataset_source_key)
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
        dataset_source_key=args.dataset_source_key,
    )
    if args.strict_main_flow:
        _check_task_compatibility(model, datasets)
        _check_dataset_input_policy(datasets)
    tasks = _dedupe([str(item.get("task") or "UNKNOWN") for item in datasets])
    languages = _dedupe([str(item.get("language") or "") for item in datasets if item.get("language")])
    metric_list = _dedupe([metric for item in datasets for metric in item.get("default_metrics", [])])
    allowed_local_runtimes = list(active_site_policy["policy"]["execution"]["local_runtimes"])
    execution = _normalize_execution(
        args.execution,
        args.execution_path,
        runtime_kind,
        allowed_local_runtimes,
    )
    device = _resolve_device(args.device)

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
            "model_runtime": runtime_kind,
            "max_samples": args.max_samples,
            "deployment_binding": model["deployment_binding"],
            "harness_runtime": harness_runtime,
            "dataset_projection": dataset_projection,
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
            "datasets_root": getattr(args, "datasets_root", None),
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
            "model_runtime": runtime_kind,
            "deployment_binding": model["deployment_binding"],
            "harness_runtime": harness_runtime,
            "dataset_projection": dataset_projection,
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
    parser.add_argument("--execution", choices=("auto", "local"))
    parser.add_argument(
        "--execution-path",
        default="auto",
        choices=("local_bash", "local_docker", "local_python", "auto"),
    )
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
    parser.add_argument("--datasets-root")
    parser.add_argument("--output")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--dataset-source-key", default="default")
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
