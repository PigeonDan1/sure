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
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
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


def _metric_from_sota(sota_manager: SOTAManager, dataset_name: str) -> str | None:
    metric = sota_manager.get_metric(dataset_name)
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
    env = os.environ.copy()
    src = str(engine_root / "src")
    env["PYTHONPATH"] = f"{src}:{env.get('PYTHONPATH', '')}" if env.get("PYTHONPATH") else src
    return env


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
            [sys.executable, "-c", code],
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
            [sys.executable, "-c", code],
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
    requested_metric = metric_override or _metric_from_sota(sota_manager, canonical_name)
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
    rps = sota_manager.calculate_rps(canonical_name, score)

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
    requested_metric = metric_override or _metric_from_sota(sota_manager, canonical_name)
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
    rps = sota_manager.calculate_rps(canonical_name, score)

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

    rps = sota_manager.calculate_rps(canonical_name, score)

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
        "inputs": {
            "jsonl_path": result.get("jsonl_path"),
            "prediction_path": result.get("prediction_path"),
        },
        "artifacts": {},
    }


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


def _write_protocol_yaml(
    results_dir: Path,
    protocol_id: str,
    model_dir: Path | None,
    *,
    results: list[dict[str, Any]] | None = None,
    tool_name: str | None = None,
) -> None:
    protocol: dict[str, Any] = {}
    server: dict[str, Any] = {}
    config_yaml = model_dir / "config.yaml" if model_dir and model_dir.exists() else None
    try:
        import yaml

        if config_yaml and config_yaml.exists():
            cfg = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
            protocols = cfg.get("protocols", {})
            protocol = protocols.get(protocol_id, {}) if isinstance(protocols, dict) else {}
            server = dict(cfg.get("server") or {})
    except Exception as exc:
        logger.warning("Failed to read model config for protocol.yaml", error=str(exc))

    datasets: list[dict[str, Any]] = []
    by_dataset: dict[str, dict[str, Any]] = {}
    for result in results or []:
        dataset = str(result.get("dataset") or "")
        if not dataset:
            continue
        item = by_dataset.setdefault(
            dataset,
            {
                "name": dataset,
                "task": result.get("task"),
                "language": result.get("language"),
                "metrics": [],
                "jsonl_path": result.get("jsonl_path"),
            },
        )
        metric = str(result.get("metric") or "")
        if metric and metric not in item["metrics"]:
            item["metrics"].append(metric)
    for item in by_dataset.values():
        item["metrics"] = sorted(item["metrics"])
        datasets.append(item)

    if "env" in server:
        server["env_keys"] = sorted(str(key) for key in (server.get("env") or {}).keys())
        server.pop("env", None)

    payload = {
        "schema": "sure.eval.protocol.v1",
        "protocol_id": protocol_id,
        "model": {
            "model_dir": str(model_dir) if model_dir else None,
            "tool_name": tool_name,
            "server": server,
        },
        "datasets": datasets,
        "protocol": protocol,
        "artifact_layout": {
            "evaluation_payload": "evaluation_payload.json",
            "report_jsonl": "report.jsonl",
            "protocol": "protocol.yaml",
            "metrics": "metrics/<dataset>/<metric_slug>/",
            "sample_reports": "sample_reports/<dataset>/<metric_slug>.jsonl",
            "predictions": "predictions/<dataset>.txt and predictions/<dataset>.jsonl",
        },
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
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
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
                "metric_artifact_dir": str(metric_dir),
                "report": str(report_path),
                "pipeline_description": str(pipeline_path),
                "sample_report": str(sample_report_path),
                "prediction_file": str(Path(result["prediction_path"])),
            }
        )
        row["artifacts"] = artifacts
        pipeline_payload = dict(row.get("pipeline") or {})
        pipeline_payload.update(
            {
                "pipeline_id": row.get("pipeline_id") or result.get("pipeline_id"),
                "report_path": str(report_path),
                "description_path": str(pipeline_path),
            }
        )
        row["pipeline"] = pipeline_payload
        row["run_id"] = run_dir.name
        row["tool_uid"] = tool_name
        row["protocol_id"] = protocol_id
        report_rows.append(row)

    payload_with_artifacts = dict(payload)
    payload_with_artifacts["results"] = report_rows
    (run_dir / "evaluation_payload.json").write_text(
        json.dumps(_to_strict_jsonable(payload_with_artifacts), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_report_jsonl(run_dir / "report.jsonl", report_rows)
    snapshot_template = SKILL_REPORT_SNAPSHOT_TEMPLATE
    if snapshot_template.is_file():
        snapshot = snapshot_template.read_text(encoding="utf-8")
    else:
        snapshot = "# SURE-EVAL Report Snapshot\n\n"
    summary_lines = [
        "",
        f"- run_id: {run_dir.name}",
        f"- tool_uid: {tool_name}",
        f"- protocol_id: {protocol_id}",
        f"- num_results: {len(results)}",
    ]
    (run_dir / "report_snapshot.md").write_text(snapshot.rstrip() + "\n" + "\n".join(summary_lines) + "\n", encoding="utf-8")


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
        with report_path.open("w", encoding="utf-8") as handle:
            for result in results:
                report_line = {
                    "tool_uid": tool_name,
                    "protocol_id": protocol_id,
                    "dataset": result["dataset"],
                    "task": result["task"],
                    "language": result["language"],
                    "metric": result["metric"],
                    "score": result["score"],
                    "rps": result.get("rps"),
                    "num_samples": result["num_samples"],
                    "evaluation_backend": result.get("evaluation_backend", "legacy"),
                    "pipeline_id": result.get("pipeline_id"),
                    "evaluation_context": result.get("evaluation_context"),
                    "prediction_file": f"predictions/{result['dataset']}.txt",
                    "evaluator_version": result.get("evaluator_version", "sure_eval v1.0"),
                }
                handle.write(json.dumps(report_line, ensure_ascii=False) + "\n")
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
    parser.add_argument("--protocol-id", type=str, default="strict_core", help="Inference protocol ID")
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
                    {"source": resolved_engine[0], "engine_root": str(resolved_engine[1])}
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
                {"source": resolved_engine[0], "engine_root": str(resolved_engine[1])}
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
