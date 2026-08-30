#!/usr/bin/env python3
"""
Evaluate prepared prediction files against canonical SURE-EVAL datasets.

This script is deterministic by design:
- dataset resolution goes through DatasetManager
- metric selection goes through SOTA baseline first
- optional result recording goes through RPSManager
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.config import Config
from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.datasets import DatasetManager
from sure_eval.evaluation.rps import RPSManager
from sure_eval.evaluation.sure_evaluator import SUREEvaluator
from sure_eval.reports import SOTAManager

from resolve_evaluation_engine import resolve_engine_root
from evaluation_runtime import (
    EvaluationRuntimeError,
    ensure_evaluation_runtime,
    evaluation_child_environment,
)

configure_logging(level="INFO")
logger = get_logger(__name__)
SKILL_ROOT = Path(__file__).resolve().parent.parent
HARNESS_ROOT = Path(__file__).resolve().parents[4]
SURE_SUITES_ROOT = Path("data/datasets/sure_benchmark/SURE_Test_Suites")

LOWER_IS_BETTER_METRICS = {
    "wer",
    "cer",
    "mer",
    "der",
    "cpwer",
    "tts_wer",
    "tts_cer",
    "vc_wer",
    "vc_cer",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_run_id(run_dir: Path) -> str:
    return os.environ.get("RUN_ID") or run_dir.name


def _git_commit(root: Path | None) -> str | None:
    if root is None or not root.exists():
        return None
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=evaluation_child_environment(),
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


class ExternalEvaluationUnsupported(RuntimeError):
    """Raised when a dataset task needs the legacy in-process evaluator."""


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_prediction_map(path: Path) -> dict[str, str]:
    predictions: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            key, value = line.split("\t", 1)
        else:
            parts = line.split(None, 1)
            key = parts[0]
            value = parts[1] if len(parts) > 1 else ""
        predictions[key] = value
    return predictions


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _localize_path(value: Any, base_dir: Path | None = None) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    if base_dir is not None:
        candidate = base_dir / path
        if candidate.exists():
            return candidate
    return path


def load_structured_prediction_map(path: Path) -> dict[str, dict[str, Any]]:
    predictions: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return predictions
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = str(row.get("key", ""))
            if key:
                predictions[key] = row
    return predictions


def _samples_with_predictions(
    samples: list[dict[str, Any]],
    prediction_keys: set[str],
    *,
    dataset_name: str,
) -> list[dict[str, Any]]:
    matched = [sample for sample in samples if str(sample.get("key", "")) in prediction_keys]
    if not matched:
        raise ValueError(f"No predictions match dataset samples: {dataset_name}")
    return matched


def _write_eval_file(rows: list[str]) -> str:
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    handle.write("\n".join(rows) + "\n")
    handle.close()
    return handle.name


def _describe_evaluation_context(task: str, language: str, metric: str) -> dict[str, Any]:
    """Describe the dataset-driven post-processing used by the evaluator."""
    context: dict[str, Any] = {
        "task": task,
        "language": language,
        "language_source": "dataset_jsonl",
        "metric": metric,
        "metric_source": "sota_baseline_or_task_default",
    }

    if task == "ASR":
        context.update(
            {
                "postprocessing": "SUREEvaluator._eval_asr",
                "normalization": "sure_eval.evaluation.normalization.asr_simple_tn.asr_num2words",
                "punctuation_policy": "evaluation-pipeline clean_marks.strip_all_punct compatible",
                "tokenization": "code_switch_mer_wer_cer" if language == "cs" else "character" if metric == "cer" or language == "zh" else "word",
                "case_sensitive": False,
            }
        )
    elif task == "S2TT":
        context.update(
            {
                "postprocessing": "SUREEvaluator._eval_s2tt",
                "normalization": "sacrebleu_tokenizer_by_language",
            }
        )
    elif task in {"SER", "GR", "SLU"}:
        context.update(
            {
                "postprocessing": "evaluation-pipeline process_prediction compatible" if task == "SLU" else f"SUREEvaluator.{task.lower()}_label_normalization",
                "normalization": "prompt_option_restoration" if task == "SLU" else "label_normalization",
            }
        )
    elif task == "SA-ASR":
        context.update(
            {
                "postprocessing": "SUREEvaluator._eval_sa_asr",
                "normalization": "evaluation-pipeline text_normalizer.normalize_text compatible",
            }
        )

    return context


def _metric_from_sota(
    sota_manager: SOTAManager, dataset_name: str, fallback_names: tuple[str, ...] | list[str] = ()
) -> str | None:
    metric = sota_manager.get_metric(dataset_name, fallback_names=fallback_names)
    return str(metric) if metric else None


def _legacy_default_metric(task: str, language: str) -> str:
    return (
        "accuracy" if task in {"SER", "GR", "SLU"}
        else "bleu" if task == "S2TT"
        else "der" if task == "SD"
        else "cpwer" if task == "SA-ASR"
        else "tts_cer" if task == "TTS" and _is_chinese_family_language(language)
        else "tts_wer" if task == "TTS"
        else "mer" if task == "ASR" and language == "cs"
        else "wer" if task == "ASR" and language == "en"
        else "cer"
    )


def _legacy_metric(sota_manager: SOTAManager, dataset_name: str, task: str, language: str) -> str:
    return _metric_from_sota(sota_manager, dataset_name) or _legacy_default_metric(task, language)


def _is_chinese_family_language(language: str) -> bool:
    return str(language).lower().startswith(("zh", "cmn", "yue"))


def _legacy_metric_applies_to_task_language(metric: str | None, task: str, language: str) -> bool:
    if metric is None:
        return True
    metric_name = metric.lower()
    if task == "TTS":
        if metric_name == "tts_cer":
            return _is_chinese_family_language(language)
        if metric_name == "tts_wer":
            return not _is_chinese_family_language(language)
    if task == "VC":
        if metric_name == "vc_cer":
            return _is_chinese_family_language(language)
        if metric_name == "vc_wer":
            return not _is_chinese_family_language(language)
    return True


def _metric_task_hint(metrics: list[str | None]) -> str:
    hinted: list[str] = []
    for metric in metrics:
        metric_name = str(metric or "").strip().lower()
        if metric_name.startswith("vc_"):
            hinted.append("VC")
        elif metric_name.startswith("tts_"):
            hinted.append("TTS")
    hinted = [task for index, task in enumerate(hinted) if task not in hinted[:index]]
    return hinted[0] if len(hinted) == 1 else ""


def _effective_audio_task(dataset_task: str, task_hint: str) -> str:
    task = str(dataset_task or "").upper()
    hint = str(task_hint or "").upper()
    if task in {"TTS", "VC"} and hint in {"TTS", "VC"}:
        return hint
    return task


def _resolve_sota_file(resolved_engine: tuple[str, Path] | None) -> Path | None:
    explicit = os.environ.get("SURE_SOTA_BASELINE")
    if explicit:
        return Path(explicit).expanduser()
    candidates: list[Path] = []
    if resolved_engine is not None:
        candidates.append(resolved_engine[1] / "src" / "sure_eval" / "reports" / "sota" / "sota_baseline.yaml")
    candidates.extend(
        [
            SKILL_ROOT / "scripts" / "sure_eval" / "reports" / "sota" / "sota_baseline.yaml",
            SKILL_ROOT / "reports" / "sota" / "sota_baseline.yaml",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _write_optional_source_file(samples: list[dict[str, Any]]) -> str | None:
    rows: list[str] = []
    has_source = False
    for sample in samples:
        key = sample.get("key", "")
        value = (
            sample.get("source")
            or sample.get("src")
            or sample.get("source_text")
            or sample.get("prompt")
            or sample.get("input")
            or ""
        )
        has_source = has_source or bool(str(value).strip())
        rows.append(f"{key}\t{value}")
    if not has_source:
        return None
    return _write_eval_file(rows)


def _resolve_existing_prediction_path(value: Any, base_dir: Path) -> str:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    if not path.exists():
        raise FileNotFoundError(f"Prediction artifact does not exist: {path}")
    return str(path.resolve())


def _localize_sample_audio_path(value: Any, dataset_jsonl_path: Path) -> str:
    if value in (None, ""):
        return ""
    path = Path(str(value)).expanduser()
    candidates = [path] if path.is_absolute() else [
        dataset_jsonl_path.parent / path,
        HARNESS_ROOT / path,
        HARNESS_ROOT / SURE_SUITES_ROOT / path,
        SKILL_ROOT / path,
        SKILL_ROOT / SURE_SUITES_ROOT / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return str(path)


def _sample_reference_text(sample: dict[str, Any]) -> str:
    return str(
        sample.get("reference_text")
        or sample.get("target")
        or sample.get("target_text")
        or sample.get("text")
        or ""
    )


def _sample_reference_audio(sample: dict[str, Any]) -> str:
    return str(
        sample.get("reference_audio")
        or sample.get("prompt_audio")
        or sample.get("prompt_audio_path")
        or sample.get("path")
        or ""
    )


def _sample_source_audio(sample: dict[str, Any]) -> str:
    return str(
        sample.get("source_audio")
        or sample.get("src_audio")
        or sample.get("input_audio")
        or sample.get("path")
        or ""
    )


def _write_external_audio_samples_jsonl(
    *,
    task: str,
    dataset_jsonl_path: Path,
    samples: list[dict[str, Any]],
    structured_predictions: dict[str, dict[str, Any]],
    structured_prediction_path: Path,
    output_path: Path,
    required_roles: set[str],
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    task_upper = task.upper()
    for sample in samples:
        key = str(sample.get("key", ""))
        structured = structured_predictions.get(key)
        if structured is None:
            raise ValueError(f"Missing structured prediction row for {task_upper} sample: {key}")
        prediction = structured.get("prediction") if isinstance(structured.get("prediction"), dict) else {}
        audio_path = prediction.get("audio_path") or structured.get("normalized_prediction")
        if not audio_path:
            raise ValueError(f"Missing prediction audio_path for {task_upper} sample: {key}")
        generated_audio = _resolve_existing_prediction_path(audio_path, structured_prediction_path.parent)
        language = str(sample.get("language") or structured.get("language") or "en")
        if task_upper == "TTS":
            row = {
                "sample_id": key,
                "prediction_audio": generated_audio,
                "reference_text": _sample_reference_text(sample),
                "reference_audio": _localize_sample_audio_path(_sample_reference_audio(sample), dataset_jsonl_path),
                "language": language,
                "metadata": {
                    "dataset": structured.get("dataset"),
                    "task": "TTS",
                    "source_key": key,
                },
            }
        elif task_upper == "VC":
            row = {
                "sample_id": key,
                "converted_audio": generated_audio,
                "reference_text": _sample_reference_text(sample),
                "reference_audio": _localize_sample_audio_path(_sample_reference_audio(sample), dataset_jsonl_path),
                "source_audio": _localize_sample_audio_path(_sample_source_audio(sample), dataset_jsonl_path),
                "language": language,
                "metadata": {
                    "dataset": structured.get("dataset"),
                    "task": "VC",
                    "source_key": key,
                },
            }
        else:
            raise ExternalEvaluationUnsupported(f"task {task!r} does not use samples_jsonl audio bridge")
        missing_roles = [role for role in sorted(required_roles) if not row.get(role)]
        if missing_roles:
            raise ValueError(f"{task_upper} sample {key} is missing required role(s): {', '.join(missing_roles)}")
        rows.append(row)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return output_path


def _safe_path_component(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in value)
    return safe or "dataset"


def _external_env(engine_root: Path) -> dict[str, str]:
    env = evaluation_child_environment()
    src = str(engine_root / "src")
    env["PYTHONPATH"] = f"{src}:{env.get('PYTHONPATH', '')}" if env.get("PYTHONPATH") else src
    return env


@lru_cache(maxsize=8)
def _evaluation_runtime_binding(engine_root: Path) -> dict[str, Any]:
    return ensure_evaluation_runtime(engine_root, prepare=False)


def _evaluation_python(engine_root: Path) -> str:
    return str(_evaluation_runtime_binding(engine_root)["python_executable"])


def _describe_external_pipeline(
    *,
    engine_root: Path,
    task: str,
    language: str,
    metric: str | None,
    pipeline_id: str | None = None,
    timeout: int,
) -> dict[str, Any]:
    request_handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    request_handle.write(
        json.dumps(
            {
                "task": task,
                "language": language if language != "auto" else None,
                "metric": metric,
                "pipeline_id": pipeline_id,
            },
            ensure_ascii=False,
        )
    )
    request_handle.close()
    request_path = Path(request_handle.name)
    env = _external_env(engine_root)
    env["SURE_EVAL_BRIDGE_REQUEST"] = str(request_path)
    code = r"""
import json
import os
from pathlib import Path

from sure_eval.evaluation.cli_adapters import build_pipeline_spec

request = json.loads(Path(os.environ["SURE_EVAL_BRIDGE_REQUEST"]).read_text(encoding="utf-8"))
pipeline = build_pipeline_spec(
    request["task"],
    language=request.get("language"),
    metric=request.get("metric"),
    pipeline_id=request.get("pipeline_id"),
)
print(json.dumps(pipeline, ensure_ascii=False))
"""
    try:
        completed = subprocess.run(
            [_evaluation_python(engine_root), "-c", code],
            cwd=engine_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    finally:
        request_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise RuntimeError(
            "external sure-evaluation describe failed "
            f"(returncode={completed.returncode})\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not stdout_lines:
        raise RuntimeError("external sure-evaluation describe produced no JSON output")
    try:
        payload = json.loads(stdout_lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"external sure-evaluation describe produced invalid JSON: {exc}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("external sure-evaluation describe payload must be a JSON object")
    return payload


def _pipeline_required_roles(pipeline: dict[str, Any]) -> set[str]:
    return {str(role) for role in pipeline.get("required_roles") or [] if str(role)}


def _pipeline_uses_samples_jsonl(pipeline: dict[str, Any]) -> bool:
    roles = _pipeline_required_roles(pipeline)
    run_args = pipeline.get("run_args") if isinstance(pipeline.get("run_args"), dict) else {}
    return "samples_jsonl" in roles or "samples_jsonl" in run_args


def _pipeline_uses_text_pair(pipeline: dict[str, Any]) -> bool:
    roles = _pipeline_required_roles(pipeline)
    return {"hyp", "ref"}.issubset(roles)


def _pipeline_audio_row_required_roles(pipeline: dict[str, Any]) -> set[str]:
    run_args = pipeline.get("run_args") if isinstance(pipeline.get("run_args"), dict) else {}
    ignored = {"samples_jsonl", "output_dir", "device", "cache_dir"}
    return {str(role) for role in run_args if role not in ignored}


def _run_external_pipeline(
    *,
    engine_root: Path,
    request: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    request_handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    request_handle.write(json.dumps(request, ensure_ascii=False))
    request_handle.close()
    request_path = Path(request_handle.name)

    env = _external_env(engine_root)
    env["SURE_EVAL_BRIDGE_REQUEST"] = str(request_path)
    if str(request.get("device") or "").lower() == "cpu" and "CUDA_VISIBLE_DEVICES" not in env:
        env["CUDA_VISIBLE_DEVICES"] = ""

    code = r"""
import json
import os
from pathlib import Path

from sure_eval.evaluation.cli_adapters import build_pipeline_spec, run_pipeline_spec

request = json.loads(Path(os.environ["SURE_EVAL_BRIDGE_REQUEST"]).read_text(encoding="utf-8"))
pipeline = request.get("pipeline")
if pipeline is None:
    pipeline = build_pipeline_spec(
        request["task"],
        language=request.get("language"),
        metric=request.get("metric"),
        pipeline_id=request.get("pipeline_id"),
    )
summary = run_pipeline_spec(
    pipeline,
    output_dir=request["output_dir"],
    ref_file=request.get("ref_file"),
    hyp_file=request.get("hyp_file"),
    src_file=request.get("src_file"),
    prompt_jsonl=request.get("prompt_jsonl"),
    label_spec=request.get("label_spec"),
    reference_jsonl=request.get("reference_jsonl"),
    sample_output=request.get("sample_output"),
    samples_jsonl=request.get("samples_jsonl"),
    device=request.get("device") or "cuda",
    cache_dir=request.get("cache_dir"),
)
report_path_value = summary.get("report_path")
report_path = Path(report_path_value) if report_path_value else None
report = json.loads(report_path.read_text(encoding="utf-8")) if report_path and report_path.is_file() else None
print(json.dumps({"pipeline": pipeline, "summary": summary, "report": report}, ensure_ascii=False))
"""

    try:
        completed = subprocess.run(
            [_evaluation_python(engine_root), "-c", code],
            cwd=engine_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    finally:
        request_path.unlink(missing_ok=True)

    if completed.returncode != 0:
        raise RuntimeError(
            "external sure-evaluation run failed "
            f"(returncode={completed.returncode})\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )

    stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not stdout_lines:
        raise RuntimeError("external sure-evaluation run produced no JSON output")
    try:
        return json.loads(stdout_lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"external sure-evaluation run produced invalid JSON: {exc}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        ) from exc


def evaluate_prediction_file_external(
    dataset_manager: DatasetManager,
    sota_manager: SOTAManager,
    dataset_name: str,
    prediction_path: Path,
    *,
    engine_source: str,
    engine_root: Path,
    external_runs_dir: Path,
    device: str,
    cache_dir: str | None,
    timeout: int,
    metric_override: str | None = None,
    pipeline_id_override: str | None = None,
    task_override: str | None = None,
) -> dict[str, Any]:
    canonical_name = dataset_manager.normalize_dataset_name(dataset_name)
    jsonl_path = dataset_manager.get_jsonl_path(canonical_name)
    if not jsonl_path.exists():
        jsonl_path = dataset_manager.download_and_convert(canonical_name)

    all_samples = load_jsonl(jsonl_path)
    predictions = load_prediction_map(prediction_path)
    if not all_samples:
        raise ValueError(f"Dataset has no samples: {canonical_name}")
    samples = _samples_with_predictions(
        all_samples,
        set(predictions),
        dataset_name=canonical_name,
    )

    dataset_task = str(all_samples[0].get("task", "ASR")).upper()
    task = str(task_override or dataset_task).upper()
    language = str(all_samples[0].get("language", "auto"))
    requested_metric = metric_override or _metric_from_sota(
        sota_manager, canonical_name, fallback_names=_source_fallback_names(jsonl_path)
    )
    pipeline = _describe_external_pipeline(
        engine_root=engine_root,
        task=task,
        language=language,
        metric=None if pipeline_id_override else requested_metric,
        pipeline_id=pipeline_id_override,
        timeout=timeout,
    )
    if _pipeline_uses_samples_jsonl(pipeline):
        raise ExternalEvaluationUnsupported(
            f"task={task} metric={requested_metric or pipeline.get('metric')} requires samples_jsonl"
        )
    if not _pipeline_uses_text_pair(pipeline):
        raise ExternalEvaluationUnsupported(
            "external route requires input roles that the text-pair bridge cannot materialize: "
            f"{sorted(_pipeline_required_roles(pipeline))}"
        )
    ref_file = _write_eval_file([f"{sample.get('key', '')}\t{sample.get('target', '')}" for sample in samples])
    hyp_file = _write_eval_file([f"{sample.get('key', '')}\t{predictions.get(sample.get('key', ''), '')}" for sample in samples])
    src_file = _write_optional_source_file(samples) if task == "S2TT" else None

    metric_dir_name = _safe_path_component(str(pipeline_id_override or requested_metric or "default"))
    run_dir = external_runs_dir / _safe_path_component(canonical_name) / metric_dir_name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        external_payload = _run_external_pipeline(
            engine_root=engine_root,
            request={
                "task": task,
                "language": language if language != "auto" else None,
                "metric": None if pipeline_id_override else requested_metric,
                "pipeline_id": pipeline_id_override,
                "output_dir": str(run_dir.resolve()),
                "ref_file": ref_file,
                "hyp_file": hyp_file,
                "src_file": src_file,
                "prompt_jsonl": str(jsonl_path) if task == "SLU" else None,
                "samples_jsonl": None,
                "device": device,
                "cache_dir": cache_dir,
                "pipeline": pipeline,
            },
            timeout=timeout,
        )
    finally:
        Path(ref_file).unlink(missing_ok=True)
        Path(hyp_file).unlink(missing_ok=True)
        if src_file:
            Path(src_file).unlink(missing_ok=True)

    summary = external_payload["summary"]
    pipeline = external_payload["pipeline"]
    report = external_payload.get("report") or {}
    metric = str(summary.get("metric") or pipeline.get("metric") or requested_metric or "")
    score = summary.get("score", report.get("score", 0.0))
    rps = sota_manager.calculate_rps(
        canonical_name, score, fallback_names=_source_fallback_names(jsonl_path)
    )

    return {
        "dataset": canonical_name,
        "jsonl_path": str(jsonl_path),
        "prediction_path": str(prediction_path),
        "task": task,
        "language": str(summary.get("language") or language),
        "metric": metric,
        "score": score,
        "rps": rps,
        "rps_is_unbounded": isinstance(rps, float) and not math.isfinite(rps),
        "num_samples": len(samples),
        "expected_samples": len(all_samples),
        "provided_predictions": len(samples),
        "evaluation_backend": "external",
        "evaluator_version": "sure-evaluation",
        "pipeline_id": summary.get("pipeline_id") or pipeline.get("pipeline_id"),
        "evaluation_context": {
            "backend": "sure-evaluation",
            "engine_source": engine_source,
            "engine_root": str(engine_root),
            "evaluation_runtime": _evaluation_runtime_binding(engine_root),
            "dataset_task": dataset_task,
            "pipeline_id": summary.get("pipeline_id") or pipeline.get("pipeline_id"),
            "route_id": pipeline.get("route_id"),
            "nodes": [node.get("node_id") for node in pipeline.get("nodes", [])],
            "node_config_paths": summary.get("node_config_paths", []),
            "external_output_dir": summary.get("output_dir"),
            "requested_metric_source": (
                "cli_pipeline_id" if pipeline_id_override else
                "cli_override" if metric_override else "sota_baseline" if requested_metric else "sure-evaluation_task_manifest"
            ),
            "requested_pipeline_id": pipeline_id_override,
        },
        "details": {
            "summary": summary,
            "report": report,
            "pipeline": pipeline,
        },
    }


def evaluate_audio_prediction_file_external(
    dataset_manager: DatasetManager,
    sota_manager: SOTAManager,
    dataset_name: str,
    prediction_path: Path,
    *,
    engine_source: str,
    engine_root: Path,
    external_runs_dir: Path,
    device: str,
    cache_dir: str | None,
    timeout: int,
    metric_override: str | None,
    pipeline_id_override: str | None = None,
    task_override: str | None = None,
) -> dict[str, Any]:
    canonical_name = dataset_manager.normalize_dataset_name(dataset_name)
    jsonl_path = dataset_manager.get_jsonl_path(canonical_name)
    if not jsonl_path.exists():
        jsonl_path = dataset_manager.download_and_convert(canonical_name)

    all_samples = load_jsonl(jsonl_path)
    if not all_samples:
        raise ValueError(f"Dataset has no samples: {canonical_name}")
    dataset_task = str(all_samples[0].get("task", "")).upper()
    task = str(task_override or dataset_task).upper()
    language = str(all_samples[0].get("language") or "en")
    requested_metric = metric_override or _metric_from_sota(
        sota_manager, canonical_name, fallback_names=_source_fallback_names(jsonl_path)
    )
    pipeline = _describe_external_pipeline(
        engine_root=engine_root,
        task=task,
        language=language,
        metric=None if pipeline_id_override else requested_metric,
        pipeline_id=pipeline_id_override,
        timeout=timeout,
    )
    if not _pipeline_uses_samples_jsonl(pipeline):
        raise ExternalEvaluationUnsupported(
            f"task={task} metric={requested_metric or pipeline.get('metric')} does not use samples_jsonl"
        )

    structured_prediction_path = prediction_path.with_suffix(".jsonl")
    structured_predictions = load_structured_prediction_map(structured_prediction_path)
    if not structured_predictions:
        raise ValueError(f"{task} external evaluation requires structured predictions: {structured_prediction_path}")
    samples = _samples_with_predictions(
        all_samples,
        set(structured_predictions),
        dataset_name=canonical_name,
    )

    metric_dir_name = _safe_path_component(str(pipeline_id_override or requested_metric or "default"))
    run_dir = external_runs_dir / _safe_path_component(canonical_name) / metric_dir_name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    samples_jsonl = _write_external_audio_samples_jsonl(
        task=task,
        dataset_jsonl_path=jsonl_path,
        samples=samples,
        structured_predictions=structured_predictions,
        structured_prediction_path=structured_prediction_path,
        output_path=run_dir / "samples.jsonl",
        required_roles=_pipeline_audio_row_required_roles(pipeline),
    )

    external_payload = _run_external_pipeline(
        engine_root=engine_root,
        request={
            "task": task,
            "language": language,
            "metric": None if pipeline_id_override else requested_metric,
            "pipeline_id": pipeline_id_override,
            "output_dir": str(run_dir.resolve()),
            "samples_jsonl": str(samples_jsonl.resolve()),
            "device": device,
            "cache_dir": cache_dir,
            "pipeline": pipeline,
        },
        timeout=timeout,
    )

    summary = external_payload["summary"]
    pipeline = external_payload["pipeline"]
    report = external_payload.get("report") or {}
    metric = str(summary.get("metric") or pipeline.get("metric") or requested_metric or "")
    score = summary.get("score", report.get("score", 0.0))
    rps = sota_manager.calculate_rps(
        canonical_name, score, fallback_names=_source_fallback_names(jsonl_path)
    )

    return {
        "dataset": canonical_name,
        "jsonl_path": str(jsonl_path),
        "prediction_path": str(prediction_path),
        "prediction_jsonl_path": str(structured_prediction_path),
        "task": task,
        "language": str(summary.get("language") or language),
        "metric": metric,
        "score": score,
        "rps": rps,
        "rps_is_unbounded": isinstance(rps, float) and not math.isfinite(rps),
        "num_samples": len(samples),
        "expected_samples": len(all_samples),
        "provided_predictions": len(samples),
        "evaluation_backend": "external",
        "evaluator_version": "sure-evaluation",
        "pipeline_id": summary.get("pipeline_id") or pipeline.get("pipeline_id"),
        "evaluation_context": {
            "backend": "sure-evaluation",
            "engine_source": engine_source,
            "engine_root": str(engine_root),
            "evaluation_runtime": _evaluation_runtime_binding(engine_root),
            "dataset_task": dataset_task,
            "pipeline_id": summary.get("pipeline_id") or pipeline.get("pipeline_id"),
            "route_id": pipeline.get("route_id"),
            "nodes": [node.get("node_id") for node in pipeline.get("nodes", [])],
            "node_config_paths": summary.get("node_config_paths", []),
            "external_output_dir": summary.get("output_dir"),
            "samples_jsonl": str(samples_jsonl),
            "requested_metric_source": (
                "cli_pipeline_id" if pipeline_id_override else
                "cli_override" if metric_override else "sota_baseline" if requested_metric else "sure-evaluation_task_manifest"
            ),
            "requested_pipeline_id": pipeline_id_override,
        },
        "details": {
            "summary": summary,
            "report": report,
            "pipeline": pipeline,
        },
    }


def evaluate_prediction_file(
    dataset_manager: DatasetManager,
    sota_manager: SOTAManager,
    dataset_name: str,
    prediction_path: Path,
    metric_override: str | None = None,
) -> dict[str, Any]:
    canonical_name = dataset_manager.normalize_dataset_name(dataset_name)
    jsonl_path = dataset_manager.get_jsonl_path(canonical_name)
    if not jsonl_path.exists():
        jsonl_path = dataset_manager.download_and_convert(canonical_name)

    all_samples = load_jsonl(jsonl_path)
    predictions = load_prediction_map(prediction_path)
    if not all_samples:
        raise ValueError(f"Dataset has no samples: {canonical_name}")
    samples = _samples_with_predictions(
        all_samples,
        set(predictions),
        dataset_name=canonical_name,
    )

    task = all_samples[0].get("task", "ASR")
    language = all_samples[0].get("language", "auto")
    metric = metric_override or _legacy_metric(sota_manager, canonical_name, task, language)

    ref_file = _write_eval_file([f"{sample.get('key', '')}\t{sample.get('target', '')}" for sample in samples])
    hyp_file = _write_eval_file([f"{sample.get('key', '')}\t{predictions.get(sample.get('key', ''), '')}" for sample in samples])

    try:
        evaluator = SUREEvaluator(language=language)
        eval_kwargs: dict[str, Any] = {}
        if task == "ASR":
            eval_kwargs["tochar"] = metric == "cer"
        elif task == "SLU":
            eval_kwargs["prompt_jsonl"] = str(jsonl_path)
        result = evaluator.evaluate(task, ref_file, hyp_file, **eval_kwargs)
    finally:
        Path(ref_file).unlink(missing_ok=True)
        Path(hyp_file).unlink(missing_ok=True)

    if isinstance(result, dict):
        details = result
    else:
        details = {"score": result}

    if task == "ASR":
        score = details.get(metric, details.get("score", 0.0))
    elif task == "S2TT" and metric == "bleu_char":
        score = details.get("bleu_char", details.get("bleu", details.get("score", 0.0)))
    elif task == "S2TT":
        score = details.get(metric, details.get("score", 0.0))
    elif task in {"SER", "GR", "SLU"}:
        score = details.get("accuracy", details.get("score", 0.0))
    elif task == "SD":
        score = details.get("der", details.get("score", 0.0))
    elif task == "SA-ASR":
        score = details.get("cpwer", details.get("score", 0.0))
    else:
        score = details.get("score", 0.0)

    rps = sota_manager.calculate_rps(
        canonical_name, score, fallback_names=_source_fallback_names(jsonl_path)
    )

    return {
        "dataset": canonical_name,
        "jsonl_path": str(jsonl_path),
        "prediction_path": str(prediction_path),
        "task": task,
        "language": language,
        "metric": metric,
        "score": score,
        "rps": rps,
        "rps_is_unbounded": isinstance(rps, float) and not math.isfinite(rps),
        "num_samples": len(samples),
        "expected_samples": len(all_samples),
        "provided_predictions": len(samples),
        "evaluation_backend": "legacy",
        "evaluator_version": "sure_eval vendored v1.0",
        "evaluation_context": _describe_evaluation_context(task, language, metric),
        "details": details,
    }


def _to_strict_jsonable(value: Any) -> Any:
    """Convert Python objects into strict-JSON-safe values."""
    if isinstance(value, dict):
        return {key: _to_strict_jsonable(subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [_to_strict_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _metric_slug(metric: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in str(metric).lower())
    return slug or "metric"


def _run_relative_artifact_path(path: str | Path, run_dir: Path) -> str:
    """Return a portable artifact path, rejecting references outside the run."""
    resolved_run_dir = run_dir.resolve()
    resolved_path = Path(path).resolve()
    try:
        return resolved_path.relative_to(resolved_run_dir).as_posix()
    except ValueError as exc:
        raise ValueError(f"artifact path must stay under the run root: {resolved_path}") from exc


def _primary_result(metric: str, score: Any, report: dict[str, Any] | None = None) -> dict[str, Any]:
    numeric_score = score if isinstance(score, (int, float)) and math.isfinite(float(score)) else None
    result: dict[str, Any] = {"metric_name": metric, "score": numeric_score, "score_key": "score"}
    metric_name = str(metric).lower()
    if metric_name.endswith("wer") or metric_name in {"wer", "tts_wer", "vc_wer", "wer_canonical"}:
        result["wer"] = numeric_score
        result["score_key"] = "wer"
    if metric_name.endswith("cer") or metric_name in {"cer", "tts_cer", "vc_cer", "cer_canonical"}:
        result["cer"] = numeric_score
        result["score_key"] = "cer"
    if metric_name.startswith("sim/"):
        result["similarity"] = numeric_score
        result["score_key"] = "similarity"
    if metric_name == "dnsmos":
        ovrl = None
        if isinstance(report, dict):
            details = report.get("details")
            if isinstance(details, dict):
                ovrl = details.get("OVRL") or details.get("mean_OVRL")
            ovrl = ovrl or report.get("OVRL") or report.get("score")
        ovrl = ovrl if isinstance(ovrl, (int, float)) and math.isfinite(float(ovrl)) else numeric_score
        result["OVRL"] = ovrl
        result["mos"] = ovrl
        result["score"] = ovrl
        result["score_key"] = "OVRL"
    if metric_name in {"wv-mos", "utmos"}:
        result["mos"] = numeric_score
        result["score_key"] = "mos"
    return result


def _result_report(result: dict[str, Any]) -> dict[str, Any]:
    details = result.get("details")
    if isinstance(details, dict):
        report = details.get("report")
        if isinstance(report, dict):
            return report
    return {}


def _result_pipeline(result: dict[str, Any]) -> dict[str, Any]:
    details = result.get("details")
    if isinstance(details, dict):
        pipeline = details.get("pipeline")
        if isinstance(pipeline, dict):
            return pipeline
    return {}


def _dataset_source_fields(jsonl_path: str | Path | None) -> dict[str, Any]:
    """Source provenance from the first dataset-JSONL row's metadata."""
    if not jsonl_path:
        return {}
    row: Any = {}
    try:
        with open(jsonl_path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    break
    except Exception:
        return {}
    if not isinstance(row, dict):
        return {}
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    fields: dict[str, Any] = {}
    if meta.get("source_dataset_name"):
        fields["source_dataset_name"] = meta["source_dataset_name"]
    if meta.get("version_id"):
        fields["version_id"] = meta["version_id"]
    if meta.get("source_dataset_root"):
        fields["source_root"] = meta["source_dataset_root"]
    return fields


def _source_fallback_names(jsonl_path: str | Path | None) -> list[str]:
    name = _dataset_source_fields(jsonl_path).get("source_dataset_name")
    return [str(name)] if name else []


def _dataset_metric_row(result: dict[str, Any]) -> dict[str, Any]:
    report = _result_report(result)
    pipeline = _result_pipeline(result)
    metric = str(result.get("metric") or "")
    context = result.get("evaluation_context") if isinstance(result.get("evaluation_context"), dict) else {}
    nodes = context.get("nodes") or [node.get("node_id") for node in pipeline.get("nodes", []) if isinstance(node, dict)]
    pipeline_id = result.get("pipeline_id") or context.get("pipeline_id") or pipeline.get("pipeline_id")
    return {
        "schema": "sure.eval.payload.dataset_metric.v2",
        "dataset": result.get("dataset"),
        "task": result.get("task"),
        "language": result.get("language"),
        "metric": metric,
        "pipeline_id": pipeline_id,
        "route_id": context.get("route_id") or pipeline.get("route_id"),
        "nodes": nodes,
        "node_config_paths": context.get("node_config_paths") or [],
        "evaluation_backend": result.get("evaluation_backend"),
        "evaluator_version": result.get("evaluator_version"),
        "num_samples": result.get("num_samples"),
        "rps": result.get("rps"),
        "evaluation_context": context,
        "result": _primary_result(metric, result.get("score"), report=report),
        "pipeline": {
            "pipeline_id": pipeline_id,
            "route_id": context.get("route_id") or pipeline.get("route_id"),
            "nodes": nodes,
            "conversion_steps": pipeline.get("conversion_steps", []),
        },
        "source": _dataset_source_fields(result.get("jsonl_path")),
        "inputs": {
            "jsonl_path": result.get("jsonl_path"),
            "prediction_path": result.get("prediction_path"),
        },
        "artifacts": {},
    }


def _validation_by_dataset(validation_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = validation_payload.get("results") if isinstance(validation_payload.get("results"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        dataset = str(row.get("dataset") or "")
        if dataset:
            out[dataset] = row
    return out


def _metric_score_from_payload_row(row: dict[str, Any]) -> Any:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    score = result.get("score")
    score_key = result.get("score_key")
    if score is None and score_key:
        score = result.get(score_key)
    return score


def _is_lower_better_metric(metric: str) -> bool:
    metric_name = str(metric or "").lower()
    return metric_name in LOWER_IS_BETTER_METRICS or metric_name.endswith(("wer", "cer", "der", "mer"))


def _metric_unit(metric: str) -> str:
    metric_name = str(metric or "").lower()
    if _is_lower_better_metric(metric_name):
        return "fraction"
    if metric_name in {"accuracy", "acc"}:
        return "fraction"
    if metric_name.startswith("sim/"):
        return "similarity"
    if metric_name in {"dnsmos", "wv-mos", "utmos"}:
        return "mos"
    return "score"


def _metric_display(metric: str, score: Any) -> str:
    if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
        return "N/A"
    if _is_lower_better_metric(metric):
        return f"{float(score) * 100:.2f}%"
    return f"{float(score):.6f}"


def _pipeline_nodes(row: dict[str, Any]) -> list[Any]:
    pipeline = row.get("pipeline") if isinstance(row.get("pipeline"), dict) else {}
    candidates = row.get("nodes") or pipeline.get("nodes") or []
    nodes: list[Any] = []
    if isinstance(candidates, list):
        for node in candidates:
            if isinstance(node, dict):
                nodes.append(node)
            elif node:
                nodes.append({"node_id": str(node)})
    return nodes


def _standard_report_row_v1(
    *,
    row: dict[str, Any],
    validation: dict[str, Any],
    run_id: str,
    protocol_id: str,
    model_dir: Path | None,
    tool_name: str,
) -> dict[str, Any]:
    artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
    inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
    pipeline = row.get("pipeline") if isinstance(row.get("pipeline"), dict) else {}
    context = row.get("evaluation_context") if isinstance(row.get("evaluation_context"), dict) else {}
    metric = str(row.get("metric") or "")
    score = _metric_score_from_payload_row(row)
    prediction_file = artifacts.get("prediction_file") or inputs.get("prediction_path") or ""
    pipeline_id = row.get("pipeline_id") or pipeline.get("pipeline_id") or context.get("pipeline_id")
    validation_summary = {
        "expected_samples": validation.get("expected_samples"),
        "provided_predictions": validation.get("provided_predictions"),
        "missing_keys": validation.get("missing_keys", []),
        "extra_keys": validation.get("extra_keys", []),
        "duplicate_keys": validation.get("duplicate_keys", []),
        "empty_prediction_keys": validation.get("empty_prediction_keys", []),
        "structured_missing_keys": validation.get("structured_missing_keys", []),
        "structured_extra_keys": validation.get("structured_extra_keys", []),
        "structured_duplicate_keys": validation.get("structured_duplicate_keys", []),
        "invalid_structured_rows": validation.get("invalid_structured_rows", []),
        "structured_projection_mismatch_keys": validation.get("structured_projection_mismatch_keys", []),
        "contract_violation_keys": validation.get("contract_violation_keys", []),
        "is_valid": validation.get("is_valid"),
        "prediction_jsonl_path": validation.get("prediction_jsonl_path") or (str(Path(str(prediction_file)).with_suffix(".jsonl")) if prediction_file else None),
        "format_used": validation.get("format_used") or "jsonl+txt",
        "require_nonempty": validation.get("require_nonempty"),
    }
    return _to_strict_jsonable(
        {
            "schema": "sure.eval.report.dataset_metric.v1",
            "run": {
                "run_id": run_id,
                "protocol_id": protocol_id,
            },
            "model": {
                "model_name": model_dir.name if model_dir else tool_name,
                "model_dir": str(model_dir) if model_dir else "",
                "tool_name": tool_name,
            },
            "dataset": {
                "name": row.get("dataset"),
                "task": row.get("task"),
                "language": row.get("language"),
                "jsonl_path": inputs.get("jsonl_path") or row.get("jsonl_path") or validation.get("jsonl_path"),
                "num_samples": row.get("num_samples"),
                **_dataset_source_fields(inputs.get("jsonl_path") or row.get("jsonl_path")),
            },
            "prediction": {
                "file": prediction_file,
                "validation": validation_summary,
            },
            "metric": {
                "name": metric,
                "score": score,
                "unit": _metric_unit(metric),
                "display": _metric_display(metric, score),
                "higher_is_better": not _is_lower_better_metric(metric),
                "score_key": (row.get("result") or {}).get("score_key") if isinstance(row.get("result"), dict) else "score",
            },
            "baseline": None,
            "rps": row.get("rps"),
            "pipeline": {
                "pipeline_id": pipeline_id,
                "report_path": pipeline.get("report_path") or artifacts.get("report"),
                "description_path": pipeline.get("description_path") or artifacts.get("pipeline_description"),
                "route_id": row.get("route_id") or pipeline.get("route_id") or context.get("route_id"),
                "nodes": _pipeline_nodes(row),
                "conversion_steps": pipeline.get("conversion_steps", []),
            },
            "versions": {
                "evaluation_backend": row.get("evaluation_backend"),
                "evaluator_version": row.get("evaluator_version"),
                "sure_evaluation_engine_root": context.get("engine_root"),
                "python": sys.version.split()[0],
                "pipeline_nodes": _pipeline_nodes(row),
            },
            "artifacts": {
                "metric_artifact_dir": artifacts.get("metric_artifact_dir"),
                "sample_report": artifacts.get("sample_report"),
                "report": artifacts.get("report"),
                "pipeline_description": artifacts.get("pipeline_description"),
            },
            "status": row.get("status") or ("success" if score is not None else "failed"),
        }
    )


def _evaluation_payload_v2(
    *,
    evaluation_backend: str,
    external_engine: dict[str, Any] | None,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    return _to_strict_jsonable(
        {
            "schema": "sure.eval.payload.v2",
            "evaluation_backend": evaluation_backend,
            "external_engine": external_engine,
            "results": [_dataset_metric_row(result) for result in results],
        }
    )


def _peek_dataset_task_language(dataset_manager: DatasetManager, dataset_name: str) -> tuple[str, str]:
    jsonl_path = dataset_manager.get_jsonl_path(dataset_name)
    if not jsonl_path.exists():
        jsonl_path = dataset_manager.download_and_convert(dataset_name)
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            return str(row.get("task", "")).upper(), str(row.get("language", "auto")).lower()
    raise ValueError(f"Dataset has no samples: {dataset_name}")


def _peek_dataset_task(dataset_manager: DatasetManager, dataset_name: str) -> str:
    task, _ = _peek_dataset_task_language(dataset_manager, dataset_name)
    return task


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_nonempty_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_model_sidecar(model_dir: Path | None, relative: str) -> dict[str, Any]:
    if model_dir is None:
        return {}
    return _read_json_file(model_dir / relative)


def _load_run_sidecar(run_dir: Path, name: str) -> dict[str, Any]:
    return _read_json_file(run_dir / name)


def _existing_path_or_none(path: Path) -> str | None:
    return str(path) if path.exists() else None


def _nested_dict(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return value if isinstance(value, dict) else {}


def _nonempty_dict(*values: dict[str, Any]) -> dict[str, Any]:
    for value in values:
        if value:
            return value
    return {}


def _ensure_prediction_manifests(
    *,
    run_dir: Path,
    results: list[dict[str, Any]],
    tool_name: str,
    protocol_id: str,
) -> None:
    pred_dir = run_dir / "predictions"
    if not pred_dir.is_dir():
        return
    required_datasets = {str(result.get("dataset") or "") for result in results if result.get("dataset")}
    existing_manifest = _read_json_file(pred_dir / "manifest.json")
    existing_conversion = _read_json_file(pred_dir / "conversion_manifest.json")
    existing_coverage = {
        str(item.get("dataset") or "")
        for item in existing_manifest.get("datasets", [])
        if isinstance(item, dict) and item.get("dataset")
    }
    existing_conversion_coverage = {
        str(item.get("dataset") or "")
        for item in existing_conversion.get("datasets", [])
        if isinstance(item, dict) and item.get("dataset")
    }
    if (
        existing_manifest.get("schema") == "sure.eval.prediction_manifest.v1"
        and existing_conversion.get("schema") == "sure.eval.prediction_conversion_manifest.v1"
        and required_datasets.issubset(existing_coverage)
        and required_datasets.issubset(existing_conversion_coverage)
    ):
        return
    datasets: list[dict[str, Any]] = []
    conversions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        dataset = str(result.get("dataset") or "")
        if not dataset or dataset in seen:
            continue
        seen.add(dataset)
        prediction_path = Path(str(result.get("prediction_path") or pred_dir / f"{dataset}.txt"))
        structured_path = prediction_path.with_suffix(".jsonl")
        if not prediction_path.is_file():
            fallback = pred_dir / f"{dataset}.txt"
            prediction_path = fallback if fallback.is_file() else prediction_path
            structured_path = prediction_path.with_suffix(".jsonl")
        jsonl_exists = structured_path.is_file()
        datasets.append(
            {
                "dataset": dataset,
                "task": result.get("task"),
                "language": result.get("language"),
                "format_used": "jsonl+txt" if jsonl_exists else "txt",
                "txt": str(prediction_path),
                "jsonl": str(structured_path) if jsonl_exists else None,
                "txt_sha256": _sha256_file(prediction_path),
                "jsonl_sha256": _sha256_file(structured_path) if jsonl_exists else None,
                "num_rows": _count_nonempty_lines(prediction_path),
                "structured_num_rows": _count_nonempty_lines(structured_path) if jsonl_exists else 0,
                "protocol_id": protocol_id,
            }
        )
        conversions.append(
            {
                "dataset": dataset,
                "source_format": "existing_sure_predictions",
                "format_used": "jsonl+txt" if jsonl_exists else "txt",
                "num_rows": _count_nonempty_lines(prediction_path),
                "source_artifacts": {
                    "compatibility_tsv": str(prediction_path),
                    "structured_jsonl": str(structured_path) if jsonl_exists else None,
                },
                "steps": [
                    {
                        "name": "prediction_contract_projection",
                        "input": str(structured_path) if jsonl_exists else str(prediction_path),
                        "output": str(prediction_path),
                        "script": "scripts/evaluate_predictions.py:_ensure_prediction_manifests",
                    }
                ],
                "conversion_trace": None,
            }
        )
    if not datasets:
        return
    generated_at = _utc_now()
    manifest = {
        "schema": "sure.eval.prediction_manifest.v1",
        "generated_at": generated_at,
        "run_id": _artifact_run_id(run_dir),
        "run_dir": str(run_dir),
        "model_name": tool_name,
        "tool_name": tool_name,
        "predictions_dir": str(pred_dir),
        "datasets": datasets,
    }
    conversion_manifest = {
        "schema": "sure.eval.prediction_conversion_manifest.v1",
        "generated_at": generated_at,
        "run_id": _artifact_run_id(run_dir),
        "run_dir": str(run_dir),
        "generated_by": os.environ.get("SURE_EVAL_PREDICTION_GENERATED_BY") or "scripts/evaluate_predictions.py",
        "predictions_dir": str(pred_dir),
        "datasets": conversions,
    }
    (pred_dir / "manifest.json").write_text(json.dumps(_to_strict_jsonable(manifest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (pred_dir / "conversion_manifest.json").write_text(
        json.dumps(_to_strict_jsonable(conversion_manifest), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_protocol_yaml(
    results_dir: Path,
    protocol_id: str,
    model_dir: Path | None,
    *,
    results: list[dict[str, Any]] | None = None,
    tool_name: str | None = None,
) -> None:
    protocol_cfg: dict[str, Any] = {}
    server_cfg: dict[str, Any] = {}
    model_cfg: dict[str, Any] = {}
    config_yaml = model_dir / "config.yaml" if model_dir and model_dir.exists() else None
    try:
        import yaml

        if config_yaml and config_yaml.exists():
            model_cfg = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
            protocols = model_cfg.get("protocols", {})
            protocol_cfg = protocols.get(protocol_id, {}) if isinstance(protocols, dict) else {}
            server_cfg = dict(model_cfg.get("server") or {})
    except Exception as exc:
        logger.warning("Failed to read model config for protocol.yaml", error=str(exc))

    model_section = _safe_dict(model_cfg.get("model"))
    tool_section = model_cfg.get("tools") if isinstance(model_cfg.get("tools"), list) else []
    first_tool = tool_section[0] if tool_section and isinstance(tool_section[0], dict) else {}
    selected_tool_name = tool_name or first_tool.get("name")
    server_env = _safe_dict(server_cfg.get("env"))
    server_env_keys = sorted(str(key) for key in server_env)
    sanitized_server = dict(server_cfg)
    sanitized_server.pop("env", None)
    sanitized_server["env_keys"] = server_env_keys

    weights_manifest = _load_model_sidecar(model_dir, "artifacts/weights_manifest.json")
    build_plan = _load_model_sidecar(model_dir, "artifacts/build_plan.json")
    standard_params = _safe_dict(protocol_cfg.get("standard_params") or protocol_cfg.get("standard"))
    resolved_model_params = _safe_dict(
        protocol_cfg.get("resolved_model_params")
        or protocol_cfg.get("model_params")
        or protocol_cfg.get("params")
    )
    unmapped = _safe_dict(protocol_cfg.get("unmapped"))
    protocol_definition_path = str(
        protocol_cfg.get("definition_path")
        or os.environ.get("SURE_EVAL_PROTOCOL_DEFINITION_PATH")
        or ""
    )
    template_file = SKILL_ROOT / "scripts" / "templates" / "protocol.yaml"
    generation_status = _load_run_sidecar(results_dir, "prediction_generation_status.json")
    prediction_reuse_manifest = _load_run_sidecar(results_dir, "prediction_reuse_manifest.json")
    runtime_inventory = _load_model_sidecar(model_dir, "artifacts/runtime_inventory.json")
    status_runtime = _safe_dict(generation_status.get("runtime"))
    status_env = _safe_dict(generation_status.get("environment"))
    status_generation = _safe_dict(generation_status.get("generation"))
    protocol_resolution = _safe_dict(status_generation.get("protocol_resolution"))
    status_runtime_inventory = _nested_dict(status_runtime, "runtime_inventory")
    harness_runtime = _safe_dict(status_runtime.get("harness_runtime"))
    inventory_container = _safe_dict(runtime_inventory.get("container_runtime"))
    inventory_local = _safe_dict(runtime_inventory.get("local_runtime"))
    inventory_policy = _safe_dict(runtime_inventory.get("policy"))
    status_server_config = _safe_dict(status_runtime.get("server_config"))
    server_command = status_runtime.get("server_command") or sanitized_server.get("command", [])
    server_working_dir = status_runtime.get("server_working_dir") or sanitized_server.get("working_dir", ".")
    env_keys = status_env.get("env_keys") if isinstance(status_env.get("env_keys"), list) else server_env_keys
    safe_env_values = _safe_dict(status_env.get("safe_env_values"))
    redacted_env_keys = status_env.get("redacted_env_keys") if isinstance(status_env.get("redacted_env_keys"), list) else []
    selected_standard_params = _nonempty_dict(_safe_dict(protocol_resolution.get("standard_params")), standard_params)
    selected_model_params = _nonempty_dict(_safe_dict(protocol_resolution.get("model_params")), resolved_model_params)
    selected_unmapped = _nonempty_dict(_safe_dict(protocol_resolution.get("unmapped")), unmapped)
    explicit_tool_args = _safe_dict(status_generation.get("tool_args"))
    argument_policy = _safe_dict(status_generation.get("argument_policy"))
    raw_response_observation = _safe_dict(status_generation.get("observed_raw_response"))
    runtime_inventory_path = (
        _existing_path_or_none(model_dir / "artifacts" / "runtime_inventory.json")
        if model_dir
        else None
    )
    generation_status_path = _existing_path_or_none(results_dir / "prediction_generation_status.json")
    source_reuse = _safe_dict(prediction_reuse_manifest.get("source"))
    source_provenance_manifest = _safe_dict(prediction_reuse_manifest.get("source_inference_provenance"))
    source_inference_provenance = _nonempty_dict(
        _safe_dict(source_reuse.get("source_inference_provenance")),
        _safe_dict(source_provenance_manifest.get("source_inference_provenance")),
    )
    prediction_reuse_enabled = bool(prediction_reuse_manifest)
    engine_root = next(
        (
            Path(str(context["engine_root"]))
            for row in results or []
            if isinstance(row, dict)
            for context in [row.get("evaluation_context")]
            if isinstance(context, dict) and context.get("engine_root")
        ),
        None,
    )
    evaluation_runtime = next(
        (
            context.get("evaluation_runtime")
            for row in results or []
            if isinstance(row, dict)
            for context in [row.get("evaluation_context")]
            if isinstance(context, dict) and isinstance(context.get("evaluation_runtime"), dict)
        ),
        {},
    )
    execution_entrypoint = os.environ.get("SURE_EVAL_EXECUTION_ENTRYPOINT")

    payload = {
        "schema": "sure.eval.inference_protocol.v1",
        "protocol_id": protocol_id,
        "run": {
            "run_id": _artifact_run_id(results_dir),
            "run_dir": str(results_dir),
            "created_at": _utc_now(),
        },
        "model": {
            "model_name": str(model_section.get("name") or model_cfg.get("name") or (model_dir.name if model_dir else tool_name or "unknown")),
            "model_dir": str(model_dir) if model_dir else None,
            "model_source": model_section.get("source") or weights_manifest.get("model_id") or weights_manifest.get("source") or None,
            "weights_source": weights_manifest.get("snapshot_path") or weights_manifest.get("local_path") or weights_manifest.get("model_path") or None,
            "model_dir_source": build_plan.get("model_dir_source") or build_plan.get("source") or None,
            "mcp_tool_name": selected_tool_name,
            "server_config": {
                "command": server_command,
                "working_dir": server_working_dir,
                "timeout": status_server_config.get("timeout", sanitized_server.get("timeout")),
                "startup_timeout_sec": status_server_config.get("startup_timeout_sec", sanitized_server.get("startup_timeout_sec")),
                "env_keys": env_keys,
            },
        },
        "protocol_selection": {
            "protocol_id": protocol_id,
            "definition_path": protocol_definition_path,
            "model_protocol_config_path": str(config_yaml) if config_yaml and config_yaml.exists() else None,
            "is_default": protocol_id == str(model_cfg.get("default_protocol") or "standard_system"),
            "purpose": protocol_cfg.get("purpose") or "standardized model inference before route-backed evaluation",
            "standard_params": selected_standard_params,
            "resolved_model_params": selected_model_params,
            "unmapped": selected_unmapped,
            "parameter_status": _safe_dict(protocol_resolution.get("parameter_status")),
            "config_sources": protocol_resolution.get("config_sources") or [],
            "resolution_status": protocol_resolution.get("status"),
            "resolution_error": protocol_resolution.get("error"),
        },
        "inference_environment": {
            "execution_path": os.environ.get("SURE_EVAL_EXECUTION_PATH", "unknown"),
            "vc": {
                "job_id": os.environ.get("SURE_EVAL_EXECUTION_JOB_ID") or os.environ.get("VC_JOB_ID"),
                "partition": os.environ.get("SURE_EVAL_VC_PARTITION") or os.environ.get("VC_PARTITION"),
                "gpu_count": os.environ.get("SURE_EVAL_VC_GPU") or os.environ.get("VC_GPU"),
                "memory": os.environ.get("SURE_EVAL_VC_MEMORY") or os.environ.get("VC_MEMORY"),
                "cpu_count": os.environ.get("SURE_EVAL_VC_CPU") or os.environ.get("VC_CPU"),
                "node_count": os.environ.get("SURE_EVAL_VC_NODES") or os.environ.get("VC_NODES"),
                "require_vc_submit": _env_bool("SURE_EVAL_REQUIRE_VC_SUBMIT", False),
                "allow_partition_fallback": _env_bool("SURE_EVAL_ALLOW_PARTITION_FALLBACK", False),
                "preflight_required": True,
            },
            "container": {
                "image": inventory_container.get("target_image"),
                "image_digest": inventory_container.get("target_image_digest"),
                "image_ref": inventory_container.get("target_image_ref") or os.environ.get("SURE_EVAL_CONTAINER_IMAGE"),
                "dockerfile": os.environ.get("SURE_EVAL_DOCKERFILE") or model_cfg.get("dockerfile"),
                "repo_root": os.environ.get("SURE_EVAL_CONTAINER_REPO_ROOT") or str(HARNESS_ROOT),
                "model_dir": str(model_dir) if model_dir else None,
                "python_executable": inventory_container.get("python_executable"),
                "working_dir": inventory_container.get("working_dir"),
                "execution_mode": inventory_policy.get("eval_runtime"),
                "host_python_fallback": inventory_policy.get("host_python_fallback"),
            },
            "harness_runtime": {
                "schema": harness_runtime.get("schema"),
                "runtime_id": harness_runtime.get("runtime_id"),
                "runtime_type": harness_runtime.get("runtime_type"),
                "python_executable": harness_runtime.get("python_executable"),
                "process_python_executable": harness_runtime.get("process_python_executable"),
                "lock_sha256": harness_runtime.get("lock_sha256"),
                "manifest_path": harness_runtime.get("manifest_path"),
                "runtime_root": harness_runtime.get("runtime_root"),
            },
            "evaluation_runtime": evaluation_runtime,
            "server": {
                "transport": "stdio_jsonrpc",
                "command": server_command,
                "working_dir": server_working_dir,
                "tool_name": selected_tool_name,
                "startup_timeout_sec": status_server_config.get("startup_timeout_sec", sanitized_server.get("startup_timeout_sec")),
                "timeout": status_server_config.get("timeout", sanitized_server.get("timeout")),
            },
            "env": {
                "device": os.environ.get("SURE_EVAL_DEVICE_ACTUAL") or os.environ.get("DEVICE") or os.environ.get("CUDA_VISIBLE_DEVICES") or None,
                "env_keys": env_keys,
                "safe_env_values": safe_env_values,
                "redacted_env_keys": redacted_env_keys,
                "modelscope_cache": os.environ.get("MODELSCOPE_CACHE"),
            },
            "runtime_inventory": {
                "path": runtime_inventory_path,
                "status": runtime_inventory.get("status") or status_runtime_inventory.get("status"),
                "schema": runtime_inventory.get("schema"),
                "local_evidence_backend": inventory_local.get("backend"),
                "execution_mode": inventory_policy.get("eval_runtime"),
                "target_image_ref": inventory_container.get("target_image_ref"),
            },
            "mount_policy": {
                "mount_stable_absolute_roots": [
                    str(path)
                    for path in (
                        model_dir,
                        HARNESS_ROOT / "data",
                    )
                    if path is not None
                ],
                "reject_repo_internal_runtime_mount_overlays": True,
                "nfs_models_read_only": _nested_dict(inventory_container, "mount_policy").get("nfs_models_read_only"),
                "result_workspace": _nested_dict(_nested_dict(inventory_container, "mount_policy"), "result_workspace"),
            },
        },
        "inference_constraints": {
            "no_external_lm": True,
            "no_retrieval": True,
            "no_hotwords": True,
            "single_pass_decode": True,
            "no_prompt_engineering": True,
            "local_fallback_allowed": False,
            "metric_logic_in_inference_image_allowed": False,
            "required_preflight_checks": [
                "deterministic_prediction_contract",
                "execution_surface_isolation",
                "model_server_smoke",
            ],
        },
        "inference_parameters": {
            "source_priority": [
                "prediction_generation_status.json",
                "runtime_inventory.json",
                "model config.yaml protocols",
                "explicit MCP tool arguments",
            ],
            "protocol_id": protocol_id,
            "protocol_resolution": {
                "status": protocol_resolution.get("status"),
                "error": protocol_resolution.get("error"),
                "standard_params": selected_standard_params,
                "model_params": selected_model_params,
                "unmapped": selected_unmapped,
            },
            "explicit_tool_args": explicit_tool_args,
            "argument_policy": argument_policy,
            "raw_response_observation": raw_response_observation,
            "model_config_protocol": {
                "standard_params": standard_params,
                "resolved_model_params": resolved_model_params,
                "unmapped": unmapped,
            },
        },
        "execution_surface": {
            "materialized": bool(execution_entrypoint) or (results_dir / "run_evaluation.sh").is_file(),
            "execution_surface_type": os.environ.get("SURE_EVAL_EXECUTION_SURFACE_TYPE") or "main_flow_script",
            "entrypoint_path": execution_entrypoint or (str(results_dir / "run_evaluation.sh") if (results_dir / "run_evaluation.sh").is_file() else None),
            "generation_method": os.environ.get("SURE_EVAL_EXECUTION_GENERATION_METHOD") or "harness_template",
            "template_file": os.environ.get("SURE_EVAL_EXECUTION_TEMPLATE_FILE"),
            "template_sha256": os.environ.get("SURE_EVAL_EXECUTION_TEMPLATE_SHA256") or _sha256_file(template_file),
            "isolation_compliance": {
                "eval_runs_referenced": False,
                "prior_run_scripts_copied": False,
                "deviation_approved_by_user": False,
            },
        },
        "prediction_reuse": {
            "enabled": prediction_reuse_enabled,
            "generation_policy": "reused_predictions_no_inference" if prediction_reuse_enabled else "generated_by_model_server",
            "manifest": _existing_path_or_none(results_dir / "prediction_reuse_manifest.json"),
            "source_run_dir": source_reuse.get("source_run_dir"),
            "source_results_dir": source_reuse.get("source_results_dir"),
            "source_run_id": source_reuse.get("source_run_id"),
            "source_protocol": source_inference_provenance.get("source_protocol"),
            "source_prediction_generation_status": source_inference_provenance.get("source_prediction_generation_status"),
            "source_runtime_inventory": source_inference_provenance.get("source_runtime_inventory"),
            "old_evaluation_reused": False,
        },
        "prediction_contract": {
            "contract_path": "references/contracts/prediction_output_contract.md",
            "compatibility_tsv": "predictions/<dataset>.txt",
            "structured_jsonl": "predictions/<dataset>.jsonl",
            "format_used": "jsonl+txt",
            "generated_by": os.environ.get("SURE_EVAL_PREDICTION_GENERATED_BY") or "scripts/generate_predictions_via_server.py",
            "protocol_argument": protocol_id,
        },
        "provenance": {
            "harness_commit": _git_commit(HARNESS_ROOT),
            "evaluation_engine": {
                "root": str(engine_root) if engine_root else None,
                "commit": _git_commit(engine_root),
            },
            "prediction_generation_status": generation_status_path,
            "prediction_generation_status_schema": generation_status.get("schema"),
            "runtime_inventory": runtime_inventory_path,
            "runtime_inventory_schema": runtime_inventory.get("schema"),
            "deployment_ready": _existing_path_or_none(model_dir / "artifacts" / "deployment_ready.json") if model_dir else None,
            "package_gate": _existing_path_or_none(model_dir / "artifacts" / "package_gate.json") if model_dir else None,
            "source_inference_provenance_manifest": _existing_path_or_none(results_dir / "source_inference_provenance.json"),
            "source_protocol": source_inference_provenance.get("source_protocol"),
            "source_prediction_generation_status": source_inference_provenance.get("source_prediction_generation_status"),
            "source_runtime_inventory": source_inference_provenance.get("source_runtime_inventory"),
            "raw_response_source_of_truth": False,
            "notes": [
                "Inference parameters come from model config, CLI overrides, protocol resolver output, and the actual MCP call policy.",
                "raw_response is preserved in predictions JSONL as model output evidence only.",
            ],
        },
        "notes": [
            "This file records inference protocol, runtime environment, inference parameters, and inference constraints only.",
            "Dataset scope, evaluation routes, metric results, validation, and metric artifacts are recorded in report_snapshot.md and report.jsonl.",
        ],
    }
    protocol_yaml = results_dir / "protocol.yaml"
    protocol_yaml.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml

        text = yaml.safe_dump(_to_strict_jsonable(payload), allow_unicode=True, sort_keys=False)
    except Exception as exc:
        logger.warning("Falling back to JSON-compatible protocol.yaml", error=str(exc))
        text = json.dumps(_to_strict_jsonable(payload), indent=2, ensure_ascii=False) + "\n"
    protocol_yaml.write_text(text, encoding="utf-8")
    logger.info("Wrote protocol.yaml", path=str(protocol_yaml))


def _copy_or_write_json(source: Path | None, destination: Path, fallback: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source and source.is_file():
        shutil.copy2(source, destination)
        return
    destination.write_text(json.dumps(_to_strict_jsonable(fallback), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _external_artifact_path(result: dict[str, Any], key: str) -> Path | None:
    direct_value = result.get(key)
    if direct_value:
        direct_path = Path(str(direct_value))
        if direct_path.is_file():
            return direct_path
    alternate_key = {
        "report_path": "metric_report_path",
        "pipeline_description_path": "pipeline_description_path",
    }.get(key)
    if alternate_key and result.get(alternate_key):
        alternate_path = Path(str(result[alternate_key]))
        if alternate_path.is_file():
            return alternate_path
    metric_artifact_dir = result.get("metric_artifact_dir")
    if metric_artifact_dir:
        filename = "report.json" if key == "report_path" else "pipeline_description.json"
        artifact_path = Path(str(metric_artifact_dir)) / filename
        if artifact_path.is_file():
            return artifact_path
    details = result.get("details")
    if not isinstance(details, dict):
        return None
    summary = details.get("summary")
    if not isinstance(summary, dict):
        return None
    value = summary.get(key)
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_file() else None


def _write_sample_report(
    *,
    output_path: Path,
    samples: list[dict[str, Any]],
    predictions: dict[str, str],
    result: dict[str, Any],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metric = str(result.get("metric") or "")
    task = str(result.get("task") or "").upper()
    calculator_cls = None
    characterize_fn = None
    if task in {"ASR", "S2TT"}:
        try:
            from sure_eval.evaluation.asr.wenet_compute_cer import Calculator, characterize

            calculator_cls = Calculator
            characterize_fn = characterize
        except Exception:
            calculator_cls = None
            characterize_fn = None
    with output_path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            key = str(sample.get("key", ""))
            prediction = predictions.get(key, "")
            row: dict[str, Any] = {
                "key": key,
                "dataset": result.get("dataset"),
                "task": task,
                "metric": metric,
                "prediction": prediction,
            }
            if task in {"ASR", "S2TT"}:
                reference = str(sample.get("target", ""))
                row["reference"] = reference
                if calculator_cls is not None and characterize_fn is not None:
                    calc = calculator_cls()
                    if metric == "cer":
                        ref_tokens = characterize_fn(reference)
                        pred_tokens = characterize_fn(prediction)
                    else:
                        ref_tokens = reference.upper().split()
                        pred_tokens = prediction.upper().split()
                    detail = calc.calculate(ref_tokens, pred_tokens)
                    total = detail["all"]
                    errors = detail["sub"] + detail["ins"] + detail["del"]
                    row["score"] = round(errors / total, 6) if total > 0 else 0.0
                    row["counts"] = {
                        "all": detail["all"],
                        "cor": detail["cor"],
                        "sub": detail["sub"],
                        "ins": detail["ins"],
                        "del": detail["del"],
                    }
            elif task == "TTS":
                row["generated_audio"] = prediction
                row["reference_text"] = _sample_reference_text(sample)
                row["reference_audio"] = _sample_reference_audio(sample)
            elif task == "VC":
                row["converted_audio"] = prediction
                row["reference_text"] = _sample_reference_text(sample)
                row["reference_audio"] = _sample_reference_audio(sample)
                row["source_audio"] = _sample_source_audio(sample)
            else:
                row["reference"] = sample.get("target") or sample.get("reference_text") or ""
            handle.write(json.dumps(_to_strict_jsonable(row), ensure_ascii=False) + "\n")


def _write_report_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_to_strict_jsonable(row), ensure_ascii=False) + "\n")


def _score_from_payload_result(metric: str, metric_result: dict[str, Any]) -> Any:
    score = metric_result.get("score")
    if score is not None:
        return score
    score_key = metric_result.get("score_key")
    if score_key and metric_result.get(score_key) is not None:
        return metric_result[score_key]
    metric_name = metric.lower()
    if metric_name.startswith("sim/"):
        return metric_result.get("similarity")
    if metric_name == "dnsmos":
        return metric_result.get("OVRL") or metric_result.get("mos")
    if metric_name in {"wv-mos", "utmos"}:
        return metric_result.get("mos")
    if metric_name.endswith("wer") or metric_name == "wer":
        return metric_result.get("wer")
    if metric_name.endswith("cer") or metric_name == "cer":
        return metric_result.get("cer")
    return None


def _legacy_result_from_payload_row(row: dict[str, Any], payload_path: Path) -> dict[str, Any]:
    if "result" not in row:
        return dict(row)
    metric = str(row.get("metric") or "")
    metric_result = row.get("result") if isinstance(row.get("result"), dict) else {}
    score = _score_from_payload_result(metric, metric_result)
    if score is None:
        raise ValueError(f"merged v2 payload row is missing result.score for metric {metric}")

    pipeline = row.get("pipeline") if isinstance(row.get("pipeline"), dict) else {}
    artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
    inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
    base_dir = payload_path.parent
    prediction_path = inputs.get("prediction_path") or artifacts.get("prediction_file")
    jsonl_path = inputs.get("jsonl_path") or row.get("jsonl_path")
    metric_artifact_dir = artifacts.get("metric_artifact_dir")
    report_path = pipeline.get("report_path") or artifacts.get("report")
    description_path = pipeline.get("description_path") or artifacts.get("pipeline_description")
    internal = {
        "dataset": row.get("dataset"),
        "jsonl_path": str(_localize_path(jsonl_path, base_dir)) if jsonl_path else "",
        "prediction_path": str(_localize_path(prediction_path, base_dir)) if prediction_path else "",
        "task": row.get("task"),
        "language": row.get("language"),
        "metric": metric,
        "score": score,
        "rps": row.get("rps"),
        "rps_is_unbounded": isinstance(row.get("rps"), float) and not math.isfinite(row["rps"]),
        "num_samples": row.get("num_samples") or metric_result.get("num_samples"),
        "evaluation_backend": row.get("evaluation_backend") or "external",
        "evaluator_version": row.get("evaluator_version") or "sure-evaluation",
        "pipeline_id": row.get("pipeline_id") or pipeline.get("pipeline_id"),
        "evaluation_context": row.get("evaluation_context") or {},
        "metric_artifact_dir": str(_localize_path(metric_artifact_dir, base_dir)) if metric_artifact_dir else "",
        "metric_report_path": str(_localize_path(report_path, base_dir)) if report_path else "",
        "pipeline_description_path": str(_localize_path(description_path, base_dir)) if description_path else "",
        "sample_report_path": str(_localize_path(artifacts.get("sample_report"), base_dir)) if artifacts.get("sample_report") else "",
        "details": {
            "summary": {
                "pipeline_id": row.get("pipeline_id") or pipeline.get("pipeline_id"),
                "report_path": str(_localize_path(report_path, base_dir)) if report_path else "",
                "pipeline_description_path": str(_localize_path(description_path, base_dir)) if description_path else "",
            },
            "report": _read_json_file(_localize_path(report_path, base_dir)) if report_path else {},
            "pipeline": _read_json_file(_localize_path(description_path, base_dir)) if description_path else pipeline,
            "payload_result": metric_result,
        },
    }
    if not internal["pipeline_id"] and isinstance(internal["details"]["pipeline"], dict):
        internal["pipeline_id"] = internal["details"]["pipeline"].get("pipeline_id")
    return internal


def merge_payload_results(payload_paths: list[Path]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for payload_path in payload_paths:
        payload = _read_json_file(payload_path)
        for row in payload.get("results") or []:
            if not isinstance(row, dict):
                continue
            result = _legacy_result_from_payload_row(row, payload_path)
            key = (str(result.get("dataset")), str(result.get("metric")))
            if key in seen:
                raise ValueError(f"duplicate dataset/metric result in merged payloads: {key[0]} {key[1]}")
            seen.add(key)
            results.append(result)
    if not results:
        raise ValueError("no dataset/metric results found in merge payloads")
    return results


BRIDGE_NOISE_PREFIXES = ("Traceback (", 'File "', "^", "~", "STDOUT:", "STDERR:")


def _summarize_bridge_error(exc: Exception) -> str:
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    meaningful = [line for line in lines if not line.startswith(BRIDGE_NOISE_PREFIXES)]
    return " | ".join((meaningful or lines)[-2:])[:400]


def _unsupported_request_message(
    *,
    source: str,
    dataset: str,
    task: str,
    dataset_task: str,
    language: str,
    requested: str,
    failures: list[str],
) -> str:
    message = (
        f"No requested metric/pipeline is supported by the {source} for dataset {dataset} "
        f"(task={task}, dataset_task={dataset_task}, language={language}, requested={requested})"
    )
    if failures:
        message += ". The engine rejected each request: " + "; ".join(failures)
    return message


def _external_metric_applies_to_task_language(
    *,
    engine_root: Path,
    metric: str | None,
    pipeline_id: str | None = None,
    task: str,
    language: str,
    timeout: int,
    failures: list[str] | None = None,
) -> bool:
    try:
        _describe_external_pipeline(
            engine_root=engine_root,
            task=task,
            language=language,
            metric=metric,
            pipeline_id=pipeline_id,
            timeout=timeout,
        )
    except (OSError, EvaluationRuntimeError):
        # The engine never got asked: a missing binary or an unusable evaluation
        # runtime says nothing about whether it supports this metric. Recording
        # it as "not applicable" turns a broken environment into a silent
        # "no metric is supported" at the end of the run.
        raise
    except Exception as exc:
        if failures is not None:
            failures.append(f"{pipeline_id or metric or 'default'}: {_summarize_bridge_error(exc)}")
        return False
    return True


def _write_run_artifacts(
    *,
    run_dir: Path,
    tool_name: str,
    protocol_id: str,
    model_dir: Path | None,
    payload: dict[str, Any],
    results: list[dict[str, Any]],
    validation_payload: dict[str, Any] | None = None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    _ensure_prediction_manifests(run_dir=run_dir, results=results, tool_name=tool_name, protocol_id=protocol_id)
    _write_protocol_yaml(run_dir, protocol_id, model_dir, results=results, tool_name=tool_name)

    report_rows: list[dict[str, Any]] = []
    payload_rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    dataset_metric_counts: dict[tuple[str, str], int] = {}
    for result in results:
        key = (str(result.get("dataset") or ""), str(result.get("metric") or ""))
        dataset_metric_counts[key] = dataset_metric_counts.get(key, 0) + 1
    for index, result in enumerate(results):
        dataset = str(result["dataset"])
        metric = str(result["metric"])
        slug = _metric_slug(metric)
        if dataset_metric_counts.get((dataset, metric), 0) > 1:
            slug = f"{slug}__{_safe_path_component(str(result.get('pipeline_id') or 'pipeline'))}"
        metric_dir = run_dir / "metrics" / dataset / slug
        report_path = metric_dir / "report.json"
        pipeline_path = metric_dir / "pipeline_description.json"
        report = _result_report(result) or {
            "task": result.get("task"),
            "language": result.get("language"),
            "metric": metric,
            "score": result.get("score"),
            "pipeline_id": result.get("pipeline_id"),
            "details": result.get("details"),
        }
        pipeline = _result_pipeline(result) or {
            "task": result.get("task"),
            "language": result.get("language"),
            "metric": metric,
            "pipeline_id": result.get("pipeline_id"),
            "nodes": result.get("evaluation_context", {}).get("nodes", []) if isinstance(result.get("evaluation_context"), dict) else [],
        }
        _copy_or_write_json(_external_artifact_path(result, "report_path"), report_path, report)
        _copy_or_write_json(_external_artifact_path(result, "pipeline_description_path"), pipeline_path, pipeline)

        sample_report_path = run_dir / "sample_reports" / dataset / f"{slug}.jsonl"
        source_sample_report = result.get("sample_report_path")
        if source_sample_report and Path(str(source_sample_report)).is_file():
            sample_report_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(str(source_sample_report)), sample_report_path)
        else:
            predictions = load_prediction_map(_localize_path(result["prediction_path"]))
            samples = _samples_with_predictions(
                load_jsonl(_localize_path(result["jsonl_path"])),
                set(predictions),
                dataset_name=dataset,
            )
            _write_sample_report(output_path=sample_report_path, samples=samples, predictions=predictions, result=result)

        row = payload_rows[index] if index < len(payload_rows) and isinstance(payload_rows[index], dict) else _dataset_metric_row(result)
        row = dict(row)
        artifacts = dict(row.get("artifacts") or {})
        artifacts.update(
            {
                "metric_artifact_dir": _run_relative_artifact_path(metric_dir, run_dir),
                "report": _run_relative_artifact_path(report_path, run_dir),
                "pipeline_description": _run_relative_artifact_path(pipeline_path, run_dir),
                "sample_report": _run_relative_artifact_path(sample_report_path, run_dir),
                "prediction_file": _run_relative_artifact_path(result["prediction_path"], run_dir),
            }
        )
        row["artifacts"] = artifacts
        inputs = dict(row.get("inputs") or {})
        inputs["prediction_path"] = artifacts["prediction_file"]
        row["inputs"] = inputs
        pipeline_payload = dict(row.get("pipeline") or {})
        pipeline_payload.update(
            {
                "pipeline_id": row.get("pipeline_id") or result.get("pipeline_id"),
                "report_path": artifacts["report"],
                "description_path": artifacts["pipeline_description"],
            }
        )
        row["pipeline"] = pipeline_payload
        row["run_id"] = _artifact_run_id(run_dir)
        row["tool_uid"] = tool_name
        row["protocol_id"] = protocol_id
        report_rows.append(row)

    payload_with_artifacts = dict(payload)
    payload_with_artifacts["results"] = report_rows
    (run_dir / "evaluation_payload.json").write_text(
        json.dumps(_to_strict_jsonable(payload_with_artifacts), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    validations = _validation_by_dataset(validation_payload or {})
    standard_rows = [
        _standard_report_row_v1(
            row=row,
            validation=validations.get(str(row.get("dataset") or ""), {}),
            run_id=_artifact_run_id(run_dir),
            protocol_id=protocol_id,
            model_dir=model_dir,
            tool_name=tool_name,
        )
        for row in report_rows
    ]
    _write_report_jsonl(run_dir / "report.jsonl", standard_rows)
    try:
        from generate_report_snapshot import build_snapshot

        snapshot = build_snapshot(run_dir)
    except Exception as exc:
        logger.warning("Failed to render rich report_snapshot.md; using template fallback", error=str(exc))
        snapshot_template = SKILL_REPORT_SNAPSHOT_TEMPLATE
        snapshot = snapshot_template.read_text(encoding="utf-8") if snapshot_template.is_file() else "# SURE-EVAL Report Snapshot\n\n"
    (run_dir / "report_snapshot.md").write_text(snapshot, encoding="utf-8")


SKILL_REPORT_SNAPSHOT_TEMPLATE = Path(__file__).resolve().parent / "templates" / "report_snapshot.md"


def _infer_source_run_dir(results: list[dict[str, Any]]) -> Path | None:
    for result in results:
        prediction_path = Path(str(result.get("prediction_path") or ""))
        if prediction_path.parent.name == "predictions":
            return prediction_path.parent.parent
    return None


def _write_results_dir(
    results_dir: Path,
    tool_name: str,
    protocol_id: str,
    model_dir: Path | None,
    results: list[dict[str, Any]],
    dataset_manager: DatasetManager,
    *,
    copy_source_report: bool = True,
) -> None:
    """Write a compatibility mirror under results/<model>/<protocol>."""
    results_dir.mkdir(parents=True, exist_ok=True)
    source_run_dir = _infer_source_run_dir(results)

    source_protocol = source_run_dir / "protocol.yaml" if source_run_dir else None
    if copy_source_report and source_protocol and source_protocol.exists():
        shutil.copy2(source_protocol, results_dir / "protocol.yaml")
    else:
        _write_protocol_yaml(results_dir, protocol_id, model_dir, results=results, tool_name=tool_name)

    report_path = results_dir / "report.jsonl"
    source_report = source_run_dir / "report.jsonl" if source_run_dir else None
    if copy_source_report and source_report and source_report.exists():
        shutil.copy2(source_report, report_path)
    else:
        standard_rows = [
            _standard_report_row_v1(
                row=_dataset_metric_row(result),
                validation={},
                run_id=results_dir.name,
                protocol_id=protocol_id,
                model_dir=model_dir,
                tool_name=tool_name,
            )
            for result in results
        ]
        _write_report_jsonl(report_path, standard_rows)
    logger.info("Wrote report.jsonl", path=str(report_path), num_entries=len(results))

    pred_dir = results_dir / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        prediction_path = Path(str(result["prediction_path"]))
        if prediction_path.exists():
            shutil.copy2(prediction_path, pred_dir / prediction_path.name)
        structured_prediction_path = prediction_path.with_suffix(".jsonl")
        if structured_prediction_path.exists():
            shutil.copy2(structured_prediction_path, pred_dir / structured_prediction_path.name)
    source_pred_dir = source_run_dir / "predictions" if source_run_dir else None
    for name in ("manifest.json", "conversion_manifest.json"):
        source_manifest = source_pred_dir / name if source_pred_dir else None
        if source_manifest and source_manifest.exists():
            shutil.copy2(source_manifest, pred_dir / name)
    if not (pred_dir / "manifest.json").is_file() or not (pred_dir / "conversion_manifest.json").is_file():
        mirror_results: list[dict[str, Any]] = []
        for result in results:
            item = dict(result)
            item["prediction_path"] = str(pred_dir / Path(str(result["prediction_path"])).name)
            mirror_results.append(item)
        _ensure_prediction_manifests(run_dir=results_dir, results=mirror_results, tool_name=tool_name, protocol_id=protocol_id)

    source_snapshot = source_run_dir / "report_snapshot.md" if source_run_dir else None
    if copy_source_report and source_snapshot and source_snapshot.exists():
        shutil.copy2(source_snapshot, results_dir / "report_snapshot.md")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic prediction files")
    parser.add_argument("--dataset", nargs="+", required=True, help="Dataset names to evaluate")
    parser.add_argument("--pred-dir", type=str, help="Directory containing <dataset>.txt prediction files")
    parser.add_argument("--pred", action="append", nargs=2, metavar=("DATASET", "FILE"), help="Explicit dataset-to-prediction mapping")
    parser.add_argument("--tool-name", type=str, help="Optional tool name to record in evaluation history")
    parser.add_argument("--record", action="store_true", help="Record results in the evaluation database")
    parser.add_argument("--config", type=str, help="Config path")
    parser.add_argument("--output", type=str, help="Optional JSON output path")
    parser.add_argument("--results-dir", type=str, help="Results output directory (e.g., results/asr_qwen3/strict_core)")
    parser.add_argument(
        "--protocol-id",
        choices=("standard_system", "strict_core"),
        default="standard_system",
        help="Inference protocol ID",
    )
    parser.add_argument("--model-dir", type=str, help="Model directory to extract protocol.yaml from config.yaml")
    parser.add_argument("--run-dir", type=str, help="Main-flow run directory for run-local artifacts")
    parser.add_argument("--report-jsonl", type=str, help="Optional run-local report.jsonl output path")
    parser.add_argument("--protocol-output", type=str, help="Optional run-local protocol.yaml output path")
    parser.add_argument("--metrics-dir", type=str, help="Optional metric artifact directory")
    parser.add_argument("--sample-reports-dir", type=str, help="Optional sample report directory")
    parser.add_argument("--validation-payload", type=str, help="Optional validation_payload.json path")
    parser.add_argument(
        "--merge-payload",
        action="append",
        help="Merge an existing evaluation_payload.json instead of running metrics. Repeat for segmented audio evaluation.",
    )
    parser.add_argument(
        "--metric",
        action="append",
        help="Metric to evaluate. Repeat for multiple metrics per dataset.",
    )
    parser.add_argument(
        "--pipeline-id",
        action="append",
        help="Exact standalone sure-evaluation pipeline_id to run. Repeat to compare multiple pipelines for the same metric.",
    )
    parser.add_argument(
        "--evaluation-backend",
        choices=("auto", "external", "legacy"),
        default=os.environ.get("SURE_EVALUATION_BACKEND", "auto"),
        help="Evaluation backend. auto prefers external sure-evaluation when available.",
    )
    parser.add_argument(
        "--strict-main-flow",
        action="store_true",
        help="Require canonical external sure-evaluation routing and reject legacy fallback.",
    )
    parser.add_argument("--evaluation-engine-root", type=str, help="Explicit standalone sure-evaluation repository root")
    parser.add_argument("--external-runs-dir", type=str, help="Directory for external sure-evaluation per-dataset run outputs")
    parser.add_argument(
        "--evaluation-device",
        "--device",
        dest="evaluation_device",
        default=os.environ.get("SURE_EVALUATION_DEVICE", "cuda"),
        help="Device forwarded to external audio-quality evaluation tasks",
    )
    parser.add_argument("--evaluation-cache-dir", type=str, help="Cache directory forwarded to external evaluation tasks")
    parser.add_argument(
        "--evaluation-timeout",
        type=int,
        default=int(os.environ.get("SURE_EVALUATION_TIMEOUT", "600")),
        help="Timeout in seconds for each external evaluation run",
    )
    parser.add_argument(
        "--evaluation-metric",
        action="append",
        help="Optional metric override forwarded to external sure-evaluation, e.g. wer, tts_wer, dnsmos.",
    )
    parser.add_argument(
        "--no-copy-source-report",
        action="store_true",
        help="When writing --results-dir, never copy protocol/report/snapshot from the source prediction run.",
    )
    args = parser.parse_args()
    if args.strict_main_flow and args.evaluation_backend != "external":
        raise ValueError("--strict-main-flow requires --evaluation-backend external")

    cfg = Config.from_yaml(args.config) if args.config else Config.from_env()
    dataset_manager = DatasetManager(cfg)
    rps_manager = RPSManager(cfg)

    explicit_preds = {dataset_manager.normalize_dataset_name(name): Path(path) for name, path in (args.pred or [])}
    pred_dir = Path(args.pred_dir) if args.pred_dir else None
    run_dir = Path(args.run_dir).resolve() if args.run_dir else None
    output_path = Path(args.output) if args.output else None
    artifact_run_dir = run_dir or (output_path.parent if output_path else None)
    validation_payload = _read_json_file(Path(args.validation_payload)) if args.validation_payload else (
        _read_json_file(run_dir / "validation_payload.json") if run_dir else {}
    )
    if args.external_runs_dir:
        external_runs_dir = Path(args.external_runs_dir)
    elif args.metrics_dir:
        external_runs_dir = Path(args.metrics_dir) / "_external_runs"
    elif args.results_dir:
        external_runs_dir = Path(args.results_dir) / "evaluation_runs"
    elif args.output:
        external_runs_dir = Path(args.output).parent / "evaluation_runs"
    elif pred_dir:
        external_runs_dir = pred_dir / "evaluation_runs"
    else:
        external_runs_dir = Path("evaluation_runs")

    resolved_engine = None
    if args.evaluation_backend in {"auto", "external"}:
        resolved_engine = resolve_engine_root(args.evaluation_engine_root)
        if args.evaluation_backend == "external" and resolved_engine is None:
            raise FileNotFoundError(
                "No standalone sure-evaluation engine found. "
                "Set --evaluation-engine-root or SURE_EVALUATION_HOME."
            )
    sota_file = _resolve_sota_file(resolved_engine)
    sota_manager = SOTAManager(sota_file) if sota_file is not None else SOTAManager()

    if args.merge_payload:
        results = merge_payload_results([Path(path) for path in args.merge_payload])
        payload = _to_strict_jsonable(
            _evaluation_payload_v2(
                evaluation_backend="external",
                external_engine=(
                    {
                        "source": resolved_engine[0],
                        "engine_root": str(resolved_engine[1]),
                        "runtime": _evaluation_runtime_binding(resolved_engine[1]),
                    }
                    if resolved_engine is not None
                    else None
                ),
                results=results,
            )
        )
        output = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
        print(output)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output, encoding="utf-8")
            logger.info("Wrote evaluation payload", path=str(output_path))
        if artifact_run_dir:
            _write_run_artifacts(
                run_dir=artifact_run_dir,
                tool_name=args.tool_name or "unknown",
                protocol_id=args.protocol_id,
                model_dir=Path(args.model_dir) if args.model_dir else None,
                payload=payload,
                results=results,
                validation_payload=validation_payload,
            )
        if args.results_dir:
            _write_results_dir(
                results_dir=Path(args.results_dir),
                tool_name=args.tool_name or "unknown",
                protocol_id=args.protocol_id,
                model_dir=Path(args.model_dir) if args.model_dir else None,
                results=results,
                dataset_manager=dataset_manager,
                copy_source_report=not args.no_copy_source_report,
            )
        return 0

    metric_overrides: list[str | None] = []
    for raw_metric in (args.metric or []) + (args.evaluation_metric or []):
        for item in str(raw_metric).replace(",", " ").split():
            item = item.strip()
            if item and item not in metric_overrides:
                metric_overrides.append(item)
    pipeline_overrides: list[str] = []
    for raw_pipeline_id in args.pipeline_id or []:
        for item in str(raw_pipeline_id).replace(",", " ").split():
            item = item.strip()
            if item and item not in pipeline_overrides:
                pipeline_overrides.append(item)
    if pipeline_overrides and args.evaluation_backend == "legacy":
        raise ValueError("--pipeline-id requires --evaluation-backend external or auto with an external engine")
    if not metric_overrides:
        metric_overrides = [None]
    task_hint = _metric_task_hint(metric_overrides)
    evaluation_requests: list[tuple[str | None, str | None]]
    if pipeline_overrides:
        evaluation_requests = [(None, pipeline_id) for pipeline_id in pipeline_overrides]
    else:
        evaluation_requests = [(metric, None) for metric in metric_overrides]

    results: list[dict[str, Any]] = []
    for requested_dataset in args.dataset:
        canonical_name = dataset_manager.normalize_dataset_name(requested_dataset)
        dataset_task, dataset_language = _peek_dataset_task_language(dataset_manager, canonical_name)
        effective_task = _effective_audio_task(dataset_task, task_hint)
        prediction_path = explicit_preds.get(canonical_name)
        if prediction_path is None:
            if pred_dir is None:
                raise ValueError(f"No prediction file provided for dataset: {canonical_name}")
            prediction_path = pred_dir / f"{canonical_name}.txt"
        if not prediction_path.exists():
            raise FileNotFoundError(f"Prediction file not found: {prediction_path}")

        request_failures: list[str] = []
        if args.evaluation_backend != "legacy" and resolved_engine is not None:
            applicable_requests = [
                (metric_override, pipeline_id_override)
                for metric_override, pipeline_id_override in evaluation_requests
                if _external_metric_applies_to_task_language(
                    engine_root=resolved_engine[1],
                    metric=None if pipeline_id_override else metric_override,
                    pipeline_id=pipeline_id_override,
                    task=effective_task,
                    language=dataset_language,
                    timeout=args.evaluation_timeout,
                    failures=request_failures,
                )
            ]
        else:
            applicable_requests = [
                (metric_override, None)
                for metric_override, pipeline_id_override in evaluation_requests
                if pipeline_id_override is None
                if _legacy_metric_applies_to_task_language(metric_override, effective_task, dataset_language)
            ]
        if not applicable_requests:
            requested = ", ".join(pipeline_overrides or [metric for metric in metric_overrides if metric]) or "default"
            source = "current sure-evaluation engine" if args.evaluation_backend != "legacy" and resolved_engine else "legacy evaluator"
            raise ValueError(
                _unsupported_request_message(
                    source=source,
                    dataset=canonical_name,
                    task=effective_task,
                    dataset_task=dataset_task,
                    language=dataset_language,
                    requested=requested,
                    failures=request_failures,
                )
            )

        for metric_override, pipeline_id_override in applicable_requests:
            if args.evaluation_backend == "legacy" or resolved_engine is None:
                if args.strict_main_flow:
                    raise RuntimeError("strict main-flow evaluation cannot use the legacy evaluator")
                result = evaluate_prediction_file(
                    dataset_manager,
                    sota_manager,
                    canonical_name,
                    prediction_path,
                    metric_override=metric_override,
                )
                if args.evaluation_backend == "auto" and resolved_engine is None:
                    result["evaluation_context"]["external_fallback_reason"] = "no standalone sure-evaluation engine resolved"
            else:
                engine_source, engine_root = resolved_engine
                try:
                    pipeline = _describe_external_pipeline(
                        engine_root=engine_root,
                        task=effective_task,
                        language=dataset_language,
                        metric=None if pipeline_id_override else metric_override,
                        pipeline_id=pipeline_id_override,
                        timeout=args.evaluation_timeout,
                    )
                    if _pipeline_uses_samples_jsonl(pipeline):
                        result = evaluate_audio_prediction_file_external(
                            dataset_manager,
                            sota_manager,
                            canonical_name,
                            prediction_path,
                            engine_source=engine_source,
                            engine_root=engine_root,
                            external_runs_dir=external_runs_dir,
                            device=args.evaluation_device,
                            cache_dir=args.evaluation_cache_dir,
                            timeout=args.evaluation_timeout,
                            metric_override=metric_override,
                            pipeline_id_override=pipeline_id_override,
                            task_override=effective_task,
                        )
                    else:
                        result = evaluate_prediction_file_external(
                            dataset_manager,
                            sota_manager,
                            canonical_name,
                            prediction_path,
                            engine_source=engine_source,
                            engine_root=engine_root,
                            external_runs_dir=external_runs_dir,
                            device=args.evaluation_device,
                            cache_dir=args.evaluation_cache_dir,
                            timeout=args.evaluation_timeout,
                            metric_override=metric_override,
                            pipeline_id_override=pipeline_id_override,
                            task_override=effective_task,
                        )
                except ExternalEvaluationUnsupported as exc:
                    if args.evaluation_backend == "external":
                        raise
                    logger.warning(
                        "External evaluation unsupported for dataset; falling back to legacy evaluator",
                        dataset=canonical_name,
                        reason=str(exc),
                    )
                    result = evaluate_prediction_file(
                        dataset_manager,
                        sota_manager,
                        canonical_name,
                        prediction_path,
                        metric_override=metric_override,
                    )
                    result["evaluation_context"]["external_fallback_reason"] = str(exc)
            results.append(result)

            if args.record:
                if not args.tool_name:
                    raise ValueError("--record requires --tool-name")
                rps_manager.evaluate_and_record(
                    tool_name=args.tool_name,
                    dataset=result["dataset"],
                    score=result["score"],
                    metric=result["metric"],
                    metadata={
                        "num_samples": result["num_samples"],
                        "prediction_path": result["prediction_path"],
                        "details": result["details"],
                    },
                )

    payload = _to_strict_jsonable(
        _evaluation_payload_v2(
            evaluation_backend=args.evaluation_backend,
            external_engine=(
                {
                    "source": resolved_engine[0],
                    "engine_root": str(resolved_engine[1]),
                    "runtime": _evaluation_runtime_binding(resolved_engine[1]),
                }
                if resolved_engine is not None
                else None
            ),
            results=results,
        )
    )
    output = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    print(output)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        logger.info("Wrote evaluation payload", path=str(output_path))
        model_dir = Path(args.model_dir) if args.model_dir else None
        _write_run_artifacts(
            run_dir=artifact_run_dir or output_path.parent,
            tool_name=args.tool_name or "unknown",
            protocol_id=args.protocol_id,
            model_dir=model_dir,
            payload=payload,
            results=results,
            validation_payload=validation_payload,
        )

    if args.results_dir:
        results_dir = Path(args.results_dir)
        model_dir = Path(args.model_dir) if args.model_dir else None
        _write_results_dir(
            results_dir=results_dir,
            tool_name=args.tool_name or "unknown",
            protocol_id=args.protocol_id,
            model_dir=model_dir,
            results=results,
            dataset_manager=dataset_manager,
            copy_source_report=not args.no_copy_source_report,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
