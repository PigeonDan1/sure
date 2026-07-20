#!/usr/bin/env python3
"""
Generate prediction files for one dataset by calling a model-local MCP server.

This script is the execution surface for the `wait_for_predictions` step when
the main flow chooses `direct_server_use`.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.config import Config
from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.datasets import DatasetManager

configure_logging(level="INFO")
logger = get_logger(__name__)

SURE_SUITES_ROOT = Path("data/datasets/sure_benchmark/SURE_Test_Suites")
PREDICTION_SNAPSHOT_INTERVAL = 25


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))
    return samples


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _load_build_plan(model_dir: Path) -> dict[str, Any]:
    build_plan_path = model_dir / "artifacts" / "build_plan.json"
    if not build_plan_path.exists():
        return {}
    try:
        return json.loads(build_plan_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_weights_manifest(model_dir: Path) -> dict[str, Any]:
    weights_manifest_path = model_dir / "artifacts" / "weights_manifest.json"
    if not weights_manifest_path.exists():
        return {}
    try:
        return json.loads(weights_manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_server_command(
    model_dir: Path,
    server_cfg: dict[str, Any],
    build_plan: dict[str, Any],
) -> list[str]:
    if "command" not in server_cfg:
        adapter = Path(__file__).resolve().parent / "model_wrapper_mcp_server.py"
        command = ["python", str(adapter), "--model-dir", str(model_dir)]
    else:
        command = list(server_cfg.get("command", ["python", "server.py"]))
    if not command:
        raise ValueError("server.command must not be empty")

    server_script_override = os.environ.get("SURE_EVAL_SERVER_SCRIPT_OVERRIDE")
    if server_script_override:
        if len(command) == 1:
            command.append(server_script_override)
        else:
            command[-1] = server_script_override

    preferred_python = os.environ.get("MODEL_PYTHON")
    if preferred_python and Path(command[0]).name.startswith("python"):
        command[0] = preferred_python
    elif command[0] == "python":
        venv_python = model_dir / ".venv" / "bin" / "python"
        build_plan_python = build_plan.get("venv_path")
        if venv_python.exists():
            command[0] = str(venv_python)
        elif build_plan_python:
            command[0] = str(Path(build_plan_python) / "bin" / "python")

    return command


def _infer_hf_home(weights_manifest: dict[str, Any]) -> str | None:
    for key in ("hf_home", "cache_root", "cache_dir"):
        value = weights_manifest.get(key)
        if value:
            return str(value)

    hub_cache_path = weights_manifest.get("hub_cache_path")
    if hub_cache_path:
        hub_cache = Path(str(hub_cache_path))
        if hub_cache.name == "hub":
            return str(hub_cache.parent)
        if "hub" in hub_cache.parts:
            hub_index = hub_cache.parts.index("hub")
            return str(Path(*hub_cache.parts[:hub_index]))

    snapshot_path = weights_manifest.get("snapshot_path")
    if snapshot_path:
        snapshot = Path(str(snapshot_path))
        if "hub" in snapshot.parts:
            hub_index = snapshot.parts.index("hub")
            return str(Path(*snapshot.parts[:hub_index]))

    return None


def _resolve_local_model_path(weights_manifest: dict[str, Any]) -> str | None:
    for key in ("local_path", "model_path", "checkpoint_path", "snapshot_path"):
        value = weights_manifest.get(key)
        if value and Path(str(value)).exists():
            return str(value)
    return None


def _resolve_working_dir(model_dir: Path, server_cfg: dict[str, Any]) -> Path:
    working_dir = server_cfg.get("working_dir", ".")
    return (model_dir / working_dir).resolve()


def _resolve_audio_path(repo_root: Path, sample: dict[str, Any]) -> Path:
    sample_path = Path(sample.get("path", ""))
    if sample_path.is_absolute():
        return sample_path

    sure_candidate = repo_root / SURE_SUITES_ROOT / sample_path
    if sure_candidate.exists():
        return sure_candidate

    relative_candidate = repo_root / sample_path
    if relative_candidate.exists():
        return relative_candidate

    raise FileNotFoundError(f"Unable to resolve audio path for sample: {sample}")


def _materialize_sample_audio(repo_root: Path, sample: dict[str, Any], scratch_dir: Path) -> Path:
    """Return a normal audio file path for a sample, slicing long audio if needed."""
    if sample.get("source_audio") and sample.get("begin_time") is not None and sample.get("end_time") is not None:
        source = Path(str(sample["source_audio"]))
        if not source.is_absolute():
            source = repo_root / source
        if not source.exists():
            raise FileNotFoundError(f"Unable to resolve source audio path for sample: {sample}")

        key = str(sample.get("key", "sample")).replace("/", "_")
        output_path = scratch_dir / f"{key}.wav"
        if not output_path.exists():
            start = float(sample["begin_time"])
            end = float(sample["end_time"])
            duration = max(0.01, end - start)
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{start:.3f}",
                    "-t",
                    f"{duration:.3f}",
                    "-i",
                    str(source),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    str(output_path),
                ],
                check=True,
            )
        return output_path

    return _resolve_audio_path(repo_root, sample)


def _safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def _normalize_tts_language(language: str | None) -> str:
    value = str(language or "").strip()
    normalized = value.lower().replace("_", "-")
    mapping = {
        "": "",
        "en": "English",
        "eng": "English",
        "english": "English",
        "zh": "Chinese",
        "zh-cn": "Chinese",
        "zh-hans": "Chinese",
        "cmn": "Chinese",
        "yue": "Chinese",
        "chinese": "Chinese",
        "cn": "Chinese",
    }
    return mapping.get(normalized, value)


def _normalize_task(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_")


def _split_metrics(value: str | None) -> list[str]:
    out: list[str] = []
    for item in str(value or "").replace(",", " ").split():
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return out


def _metric_task_hint(metrics: list[str]) -> str:
    hinted: list[str] = []
    for metric in metrics:
        metric_name = str(metric or "").strip().lower()
        if metric_name.startswith("vc_"):
            hinted.append("VC")
        elif metric_name.startswith("tts_"):
            hinted.append("TTS")
    hinted = [task for index, task in enumerate(hinted) if task not in hinted[:index]]
    return hinted[0] if len(hinted) == 1 else ""


def _model_task(model_cfg: dict[str, Any]) -> str:
    model_section = model_cfg.get("model") if isinstance(model_cfg.get("model"), dict) else {}
    return _normalize_task(model_section.get("task") or model_cfg.get("task") or model_cfg.get("task_type"))


def _effective_generation_task(sample_task: str, model_cfg: dict[str, Any], metrics: list[str]) -> str:
    task = _normalize_task(sample_task) or "ASR"
    if task in {"TTS", "VC"}:
        metric_task = _metric_task_hint(metrics)
        if metric_task in {"TTS", "VC"}:
            return metric_task
        declared_task = _model_task(model_cfg)
        if declared_task in {"TTS", "VC"}:
            return declared_task
    return task


def _resolve_audio_field_path(repo_root: Path, value: Any) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    candidates = [
        repo_root / SURE_SUITES_ROOT / path,
        repo_root / path,
        Path.cwd() / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return repo_root / path


def _sample_reference_audio_path(repo_root: Path, sample: dict[str, Any], fallback: Path) -> Path:
    value = (
        sample.get("reference_audio")
        or sample.get("reference_audio_path")
        or sample.get("target_audio_path")
        or sample.get("prompt_audio")
        or sample.get("prompt_audio_path")
        or sample.get("prompt_wav_path")
        or sample.get("prompt_wav")
    )
    return _resolve_audio_field_path(repo_root, value) or fallback


def _build_tool_arguments(
    *,
    repo_root: Path,
    sample: dict[str, Any],
    task: str,
    language: str,
    argument_name: str,
    audio_path: Path,
    output_audio_dir: Path,
    tool_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_name = task.upper()
    if task_name in {"TTS", "VC"}:
        key = str(sample.get("key", "sample"))
        prompt_audio_path = _sample_reference_audio_path(repo_root, sample, audio_path)

        target_text = (
            sample.get("target")
            or sample.get("reference_text")
            or sample.get("text")
            or sample.get("target_text")
            or ""
        )
        if not target_text:
            raise ValueError(f"TTS/VC sample has no target text: {key}")

        output_audio_dir.mkdir(parents=True, exist_ok=True)
        output_audio_path = str(output_audio_dir / f"{_safe_filename(key)}.wav")
        if task_name == "VC":
            arguments = {
                "source_audio_path": str(audio_path),
                "source": str(audio_path),
                "input_audio_path": str(audio_path),
                "reference_audio_path": str(prompt_audio_path),
                "target_audio_path": str(prompt_audio_path),
                "ref_audio_path": str(prompt_audio_path),
                "prompt_audio_path": str(prompt_audio_path),
                "prompt_wav_path": str(prompt_audio_path),
                "reference_text": str(target_text),
                "target_text": str(target_text),
                "text": str(target_text),
                "language": _normalize_tts_language(language or str(sample.get("language") or "")),
                "output_path": output_audio_path,
                "audio_path": output_audio_path,
                "converted_audio_path": output_audio_path,
            }
        else:
            arguments = {
                "text": str(target_text),
                "prompt_audio_path": str(prompt_audio_path),
                "prompt_wav_path": str(prompt_audio_path),
                "language": _normalize_tts_language(language or str(sample.get("language") or "")),
                "output_path": output_audio_path,
                "audio_path": output_audio_path,
            }
        prompt_text = (
            sample.get("prompt_text")
            or sample.get("ref_text")
            or sample.get("reference_text")
            or sample.get("target")
            or ""
        )
        if prompt_text:
            arguments["prompt_text"] = str(prompt_text)
            arguments["ref_text"] = str(prompt_text)
        if tool_args:
            arguments.update(tool_args)
        return arguments

    arguments: dict[str, Any] = {argument_name: str(audio_path)}
    if language:
        arguments["language"] = language
    if tool_args:
        arguments.update(tool_args)
    return arguments


def _parse_tool_args(values: list[str] | None) -> dict[str, Any]:
    """Parse repeated key=value tool argument overrides."""

    parsed: dict[str, Any] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"--tool-arg must use key=value format, got: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--tool-arg key must not be empty: {raw}")
        parsed[key] = _parse_tool_arg_value(value)
    return parsed


def _parse_tool_arg_value(value: str) -> Any:
    value = value.strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _parse_env_overrides(values: list[str] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"--env must use KEY=VALUE format, got: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--env key must not be empty: {raw}")
        parsed[key] = value
    return parsed


def _remap_legacy_model_env_path(value: str, model_dir: Path) -> str:
    legacy_model_dir = f"/workspace/sure-eval/src/sure_eval/models/{model_dir.name}"
    if value == legacy_model_dir:
        return str(model_dir)
    if value.startswith(legacy_model_dir + "/"):
        return str(model_dir) + value[len(legacy_model_dir):]
    return value


def _send_request(
    process: subprocess.Popen[str],
    request: dict[str, Any],
) -> dict[str, Any]:
    assert process.stdin is not None
    assert process.stdout is not None

    process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    process.stdin.flush()

    while True:
        line = process.stdout.readline()
        if line == "":
            raise RuntimeError("Server exited before returning a response")
        line = line.strip()
        if not line:
            continue
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            # Ignore non-JSON stderr-like spillovers accidentally written to stdout.
            continue
        if response.get("id") == request.get("id"):
            return response


def _extract_response_payload(response: dict[str, Any]) -> Any:
    if "error" in response:
        raise RuntimeError(response["error"].get("message", "Unknown server error"))

    result = response.get("result", {})
    if isinstance(result, dict) and result.get("isError"):
        content = result.get("content") or []
        message = ""
        if content and isinstance(content[0], dict):
            message = str(content[0].get("text") or "")
        raise RuntimeError(message or "Tool call returned isError=true")
    content = result.get("content", [])
    if not content:
        return result

    text = content[0].get("text", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _normalize_prediction_payload(payload: Any, *, task: str) -> tuple[str, dict[str, Any]]:
    task_name = task.upper()
    if isinstance(payload, dict):
        prediction = dict(payload.get("prediction") or {})
        if not prediction:
            prediction = dict(payload)
        if task_name in {"ASR", "S2TT"}:
            value = prediction.get("text") or prediction.get("transcript") or payload.get("text") or ""
            return str(value), {"text": str(value)}
        if task_name in {"TTS", "VC"}:
            value = (
                prediction.get("audio_path")
                or prediction.get("path")
                or prediction.get("generated_audio")
                or prediction.get("converted_audio")
                or payload.get("audio_path")
                or payload.get("path")
                or ""
            )
            normalized = {"audio_path": str(value)}
            if task_name == "VC":
                normalized["converted_audio"] = str(value)
                for key in ("source_audio_path", "reference_audio_path"):
                    if prediction.get(key) is not None:
                        normalized[key] = prediction[key]
            for key in ("sample_rate", "duration_ms"):
                if prediction.get(key) is not None:
                    normalized[key] = prediction[key]
            return str(value), normalized
        if task_name in {"SER", "GR"}:
            value = prediction.get("label") or payload.get("label") or payload.get("text") or ""
            return str(value), {"label": str(value)}
        if task_name == "SLU":
            value = prediction.get("text") or prediction.get("label") or payload.get("text") or payload.get("label") or ""
            normalized = {"text": str(value)}
            if prediction.get("label") is not None:
                normalized["label"] = prediction["label"]
            return str(value), normalized
        if task_name in {"SD", "SA-ASR"}:
            if prediction.get("segments") is not None:
                return json.dumps(prediction["segments"], ensure_ascii=False), {"segments": prediction["segments"]}
            value = prediction.get("annotation_path") or prediction.get("annotation") or payload.get("text") or ""
            return str(value), {"annotation": value}
        if task_name == "KWS":
            value = prediction.get("score") if prediction.get("score") is not None else payload.get("score", "")
            normalized = {"score": value}
            if prediction.get("events") is not None:
                normalized["events"] = prediction["events"]
            return str(value), normalized
        value = payload.get("text", "")
        return str(value), {"text": str(value)}

    value = str(payload)
    if task_name in {"TTS", "VC"}:
        normalized = {"audio_path": value}
        if task_name == "VC":
            normalized["converted_audio"] = value
        return value, normalized
    if task_name in {"SER", "GR"}:
        return value, {"label": value}
    if task_name in {"SD", "SA-ASR"}:
        return value, {"annotation": value}
    if task_name == "KWS":
        return value, {"score": value}
    return value, {"text": value}


def _load_existing_predictions(path: Path, *, exclude_keys: set[str] | None = None) -> dict[str, str]:
    predictions: dict[str, str] = {}
    if not path.exists():
        return predictions
    excluded = exclude_keys or set()
    with open(path, "r", encoding="utf-8") as handle:
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
            if key in excluded:
                continue
            if value.strip():
                predictions[key] = value
    return predictions


def _load_existing_structured_predictions(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(row.get("key", ""))
            if key:
                records[key] = row
    return records


def _write_prediction_snapshots(
    *,
    samples: list[dict[str, Any]],
    prediction_path: Path,
    structured_prediction_path: Path,
    prediction_map: dict[str, str],
    structured_map: dict[str, dict[str, Any]],
    canonical_dataset: str,
    sample_task: str,
    sample_language: str,
) -> None:
    prediction_tmp = prediction_path.with_name(f"{prediction_path.name}.tmp")
    structured_tmp = structured_prediction_path.with_name(f"{structured_prediction_path.name}.tmp")

    with open(prediction_tmp, "w", encoding="utf-8") as handle:
        for sample in samples:
            key = str(sample.get("key", ""))
            handle.write(f"{key}\t{prediction_map.get(key, '')}\n")
    prediction_tmp.replace(prediction_path)

    with open(structured_tmp, "w", encoding="utf-8") as handle:
        for sample in samples:
            key = str(sample.get("key", ""))
            row = structured_map.get(
                key,
                {
                    "key": key,
                    "dataset": canonical_dataset,
                    "task": sample_task,
                    "language": str(sample.get("language") or sample_language),
                    "prediction": {},
                    "normalized_prediction": prediction_map.get(key, ""),
                    "raw_response": None,
                },
            )
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    structured_tmp.replace(structured_prediction_path)


def _write_existing_result_log_entries(
    result_log_handle: Any,
    samples: list[dict[str, Any]],
    predictions: dict[str, str],
) -> None:
    written: set[str] = set()
    for sample in samples:
        key = str(sample.get("key", ""))
        if key in predictions:
            result_log_handle.write(f"{key}\t{predictions[key]}\n")
            written.add(key)
    for key, value in predictions.items():
        if key not in written:
            result_log_handle.write(f"{key}\t{value}\n")


def _upsert_dataset_status(
    status_path: Path,
    default_payload: dict[str, Any],
    dataset_status: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if status_path.exists():
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = dict(default_payload)
    else:
        payload = dict(default_payload)
    datasets = list(payload.get("datasets") or [])
    dataset_name = dataset_status.get("dataset")
    for index, row in enumerate(datasets):
        if row.get("dataset") == dataset_name:
            merged = dict(row)
            merged.update(dataset_status)
            datasets[index] = merged
            payload["datasets"] = datasets
            return payload, datasets[index]
    datasets.append(dict(dataset_status))
    payload["datasets"] = datasets
    return payload, datasets[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate predictions by calling a model-local MCP server")
    parser.add_argument("--model-dir", required=True, help="Resolved model directory containing config.yaml")
    parser.add_argument("--dataset", required=True, help="Canonical dataset name")
    parser.add_argument("--run-dir", required=True, help="Run directory under eval_runs")
    parser.add_argument("--tool-name", help="Tool name to call; defaults to the first configured tool")
    parser.add_argument("--argument-name", default="audio_path", help="Argument name for the audio path")
    parser.add_argument("--language", help="Optional language argument passed through to the tool")
    parser.add_argument("--max-samples", type=int, default=0, help="Optional limit for quick tests")
    parser.add_argument("--resume", action="store_true", help="Resume and skip keys already present in the prediction file")
    parser.add_argument(
        "--resume-exclude-keys-file",
        help="Optional newline-delimited keys to ignore while loading existing resume predictions.",
    )
    parser.add_argument("--config", help="Optional sure-eval config path")
    parser.add_argument(
        "--protocol",
        default="strict_core",
        help="Inference protocol ID (default: strict_core). Set to 'none' to disable.",
    )
    parser.add_argument(
        "--device",
        help="Device override for model inference (e.g., cuda:0, cuda:1, cpu). "
             "If set, overrides the DEVICE env var from config.yaml. "
             "When set to cpu, CUDA_VISIBLE_DEVICES is hidden unless already configured.",
    )
    parser.add_argument(
        "--tool-arg",
        action="append",
        default=[],
        help="Extra MCP tool argument in key=value form. Values are parsed as JSON when possible.",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="Extra model server environment override in KEY=VALUE form; repeatable.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    model_dir = Path(args.model_dir).resolve()
    run_dir = Path(args.run_dir).resolve()
    predictions_dir = run_dir / "predictions"
    logs_dir = predictions_dir / "logs"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    cfg = Config.from_yaml(args.config) if args.config else Config.from_env()
    dataset_manager = DatasetManager(cfg)
    expanded = dataset_manager.expand_dataset_names([args.dataset])
    if len(expanded) != 1 or dataset_manager.normalize_dataset_name(args.dataset) != expanded[0]:
        raise ValueError(
            "generate_predictions_via_server.py expects one concrete dataset split; "
            f"{args.dataset!r} expands to {expanded}"
        )
    canonical_dataset = dataset_manager.normalize_dataset_name(args.dataset)
    jsonl_path = dataset_manager.get_jsonl_path(canonical_dataset)
    if not jsonl_path.exists():
        jsonl_path = dataset_manager.download_and_convert(canonical_dataset)

    samples = _load_jsonl(jsonl_path)
    if args.max_samples > 0:
        samples = samples[: args.max_samples]
    sample_task = str(samples[0].get("task", "ASR")) if samples else "ASR"
    sample_language = str(samples[0].get("language", "")) if samples else ""

    model_cfg = _load_yaml(model_dir / "config.yaml")
    sample_task = _effective_generation_task(
        sample_task,
        model_cfg,
        _split_metrics(os.environ.get("SURE_EVAL_METRICS") or os.environ.get("METRICS")),
    )
    build_plan = _load_build_plan(model_dir)
    weights_manifest = _load_weights_manifest(model_dir)
    server_cfg = model_cfg.get("server", {})
    command = _resolve_server_command(model_dir, server_cfg, build_plan)
    working_dir = _resolve_working_dir(model_dir, server_cfg)
    env = os.environ.copy()
    for key, value in (server_cfg.get("env", {}) or {}).items():
        env[str(key)] = _remap_legacy_model_env_path(str(value), model_dir)

    # Override DEVICE if --device is explicitly provided
    if args.device:
        env["DEVICE"] = str(args.device)
        if str(args.device).lower() == "cpu" and "CUDA_VISIBLE_DEVICES" not in env:
            env["CUDA_VISIBLE_DEVICES"] = ""
    env.update(_parse_env_overrides(args.env))

    # Inject protocol parameters into environment (backward-compatible)
    if args.protocol and args.protocol.lower() != "none":
        env["SURE_EVAL_PROTOCOL_ID"] = args.protocol
        # Load protocol resolver if available
        try:
            from sure_eval.protocols.resolver import ProtocolResolver
            from sure_eval.models.registry import ModelRegistry

            resolver = ProtocolResolver()
            registry = ModelRegistry(model_dir.parent)
            model_info = registry.get_model(model_dir.name)
            if model_info is not None:
                resolved = resolver.resolve(args.protocol, model_info)
                for key, value in resolved.standard_params.items():
                    env[f"SURE_EVAL_PROTOCOL_{key.upper()}"] = str(value)
                for key, value in resolved.model_params.items():
                    env[f"SURE_EVAL_MODEL_{key.upper()}"] = str(value)
        except Exception:
            # Protocol system not available or resolution failed — silent fallback
            pass

    local_model_path = _resolve_local_model_path(weights_manifest)
    configured_model_path = env.get("MODEL_PATH")
    if local_model_path and (not configured_model_path or not Path(configured_model_path).exists()):
        env["MODEL_PATH"] = local_model_path

    inferred_hf_home = _infer_hf_home(weights_manifest)
    if inferred_hf_home and not env.get("HF_HOME"):
        env["HF_HOME"] = inferred_hf_home
    if build_plan.get("hf_cache_path") and not env.get("HF_HOME"):
        env["HF_HOME"] = str(build_plan["hf_cache_path"])

    tools = model_cfg.get("tools", [])
    tool_name = args.tool_name or (tools[0]["name"] if tools else None)
    if not tool_name:
        raise ValueError("No tool name provided and config.yaml has no tools entry")
    tool_args = _parse_tool_args(args.tool_arg)

    prediction_path = predictions_dir / f"{canonical_dataset}.txt"
    structured_prediction_path = predictions_dir / f"{canonical_dataset}.jsonl"
    output_audio_dir = predictions_dir / "audio" / canonical_dataset
    log_path = logs_dir / f"{canonical_dataset}.log"
    result_log_path = logs_dir / f"{canonical_dataset}_results.log"
    status_path = run_dir / "prediction_generation_status.json"

    resume_exclude_keys: set[str] = set()
    if args.resume and args.resume_exclude_keys_file:
        exclude_path = Path(args.resume_exclude_keys_file)
        resume_exclude_keys = {
            line.strip()
            for line in exclude_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    existing_predictions = _load_existing_predictions(prediction_path, exclude_keys=resume_exclude_keys) if args.resume else {}
    if args.resume:
        existing_predictions.update(_load_existing_predictions(result_log_path, exclude_keys=resume_exclude_keys))
    existing_structured = _load_existing_structured_predictions(structured_prediction_path) if args.resume else {}
    prediction_map = dict(existing_predictions)
    structured_map = dict(existing_structured)

    default_status_payload: dict[str, Any] = {
        "run_id": run_dir.name,
        "model_name": model_dir.name,
        "execution_path": os.environ.get("SURE_EVAL_EXECUTION_PATH", "unknown"),
        "execution_requested": os.environ.get("SURE_EVAL_EXECUTION_REQUESTED", ""),
        "execution_job_id": os.environ.get("SURE_EVAL_EXECUTION_JOB_ID", ""),
        "inference_call_mode": "direct_server_use",
        "protocol_id": args.protocol if args.protocol.lower() != "none" else None,
        "tool_name": tool_name,
        "host": socket.gethostname(),
        "device_request": os.environ.get("SURE_EVAL_DEVICE_REQUEST", args.device or ""),
        "device_actual": os.environ.get("SURE_EVAL_DEVICE_ACTUAL", args.device or ""),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
    }
    dataset_status = {
        "dataset": canonical_dataset,
        "prediction_file": str(prediction_path),
        "structured_prediction_file": str(structured_prediction_path),
        "status": "running",
        "num_expected_samples": len(samples),
        "num_generated_samples": len(prediction_map),
        "log_path": str(log_path),
        "result_log_path": str(result_log_path),
        "error": None,
    }
    status_payload, current_dataset_status = _upsert_dataset_status(status_path, default_status_payload, dataset_status)
    status_path.write_text(json.dumps(status_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with open(log_path, "w", encoding="utf-8") as log_handle, open(result_log_path, "w", encoding="utf-8") as result_log_handle:
        if args.resume and existing_predictions:
            _write_existing_result_log_entries(result_log_handle, samples, existing_predictions)
            result_log_handle.flush()

        process = subprocess.Popen(
            command,
            cwd=str(working_dir),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_handle,
            text=True,
            bufsize=1,
        )

        try:
            initialize = _send_request(
                process,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            if "error" in initialize:
                raise RuntimeError(initialize["error"].get("message", "initialize failed"))

            tools_list = _send_request(
                process,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            if "error" in tools_list:
                raise RuntimeError(tools_list["error"].get("message", "tools/list failed"))

            next_id = 3
            with tempfile.TemporaryDirectory(prefix=f"sure-eval-{canonical_dataset}-audio-") as scratch:
                scratch_dir = Path(scratch)
                for sample in samples:
                    key = str(sample.get("key", ""))
                    if args.resume and key in prediction_map:
                        continue

                    audio_path = _materialize_sample_audio(repo_root, sample, scratch_dir)
                    arguments = _build_tool_arguments(
                        repo_root=repo_root,
                        sample=sample,
                        task=sample_task,
                        language=args.language or sample_language,
                        argument_name=args.argument_name,
                        audio_path=audio_path,
                        output_audio_dir=output_audio_dir,
                        tool_args=tool_args,
                    )

                    response = _send_request(
                        process,
                        {
                            "jsonrpc": "2.0",
                            "id": next_id,
                            "method": "tools/call",
                            "params": {"name": tool_name, "arguments": arguments},
                        },
                    )
                    next_id += 1
                    raw_payload = _extract_response_payload(response)
                    prediction, normalized_prediction = _normalize_prediction_payload(raw_payload, task=sample_task)
                    prediction_map[key] = prediction
                    structured_map[key] = {
                        "key": key,
                        "dataset": canonical_dataset,
                        "task": sample_task,
                        "language": str(sample.get("language") or sample_language),
                        "prediction": normalized_prediction,
                        "normalized_prediction": prediction,
                        "raw_response": raw_payload,
                    }
                    result_log_handle.write(f"{key}\t{prediction}\n")
                    result_log_handle.flush()

                    current_dataset_status["num_generated_samples"] = len(prediction_map)
                    status_path.write_text(
                        json.dumps(status_payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    if len(prediction_map) % PREDICTION_SNAPSHOT_INTERVAL == 0:
                        _write_prediction_snapshots(
                            samples=samples,
                            prediction_path=prediction_path,
                            structured_prediction_path=structured_prediction_path,
                            prediction_map=prediction_map,
                            structured_map=structured_map,
                            canonical_dataset=canonical_dataset,
                            sample_task=sample_task,
                            sample_language=sample_language,
                        )

            _write_prediction_snapshots(
                samples=samples,
                prediction_path=prediction_path,
                structured_prediction_path=structured_prediction_path,
                prediction_map=prediction_map,
                structured_map=structured_map,
                canonical_dataset=canonical_dataset,
                sample_task=sample_task,
                sample_language=sample_language,
            )

            current_dataset_status["status"] = "completed"
            current_dataset_status["num_generated_samples"] = len(samples)
            status_path.write_text(
                json.dumps(status_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        except Exception as exc:
            current_dataset_status["status"] = "failed"
            current_dataset_status["error"] = str(exc)
            current_dataset_status["num_generated_samples"] = len(prediction_map)
            _write_prediction_snapshots(
                samples=samples,
                prediction_path=prediction_path,
                structured_prediction_path=structured_prediction_path,
                prediction_map=prediction_map,
                structured_map=structured_map,
                canonical_dataset=canonical_dataset,
                sample_task=sample_task,
                sample_language=sample_language,
            )
            status_path.write_text(
                json.dumps(status_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            raise
        finally:
            try:
                _send_request(
                    process,
                    {"jsonrpc": "2.0", "id": 999999, "method": "shutdown", "params": {}},
                )
            except Exception:
                pass
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=30)

    logger.info(
        "Generated predictions via model-local server",
        dataset=canonical_dataset,
        prediction_file=str(prediction_path),
        result_log_file=str(result_log_path),
        status_file=str(status_path),
    )
    print(
        json.dumps(
            {
                "dataset": canonical_dataset,
                "prediction_file": str(prediction_path),
                "structured_prediction_file": str(structured_prediction_path),
                "result_log_file": str(result_log_path),
                "status_file": str(status_path),
                "protocol_id": args.protocol if args.protocol.lower() != "none" else None,
                "num_samples": len(samples),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
