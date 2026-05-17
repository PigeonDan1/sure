"""Runner for the unified prediction surface."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml

from sure_eval.inference.adapters import PredictAdapter, create_predict_adapter
from sure_eval.inference.errors import AdapterError, InferenceSurfaceError, SchemaValidationError
from sure_eval.inference.language import map_language_for_model
from sure_eval.inference.schemas import (
    PREDICTION_MANIFEST_SCHEMA_VERSION,
    PREDICTION_SCHEMA_VERSION,
    validate_input_record,
    validate_prediction_file,
    validate_prediction_record,
)
from sure_eval.models.registry import ModelInfo


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_jsonl_records(input_path: str | Path, *, expected_task: str | None = None) -> list[dict[str, Any]]:
    path = Path(input_path)
    if not path.exists():
        raise SchemaValidationError(f"Input file not found: {path}")

    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                raise SchemaValidationError(f"Line {line_number} in {path} is empty.")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaValidationError(
                    f"Line {line_number} in {path} is not valid JSON: {exc}"
                ) from exc
            try:
                records.append(validate_input_record(payload, expected_task=expected_task))
            except SchemaValidationError as exc:
                raise SchemaValidationError(f"{path}:{line_number}: {exc}") from exc
    return records


def _build_command_string(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _resolve_runtime_command(model_info: ModelInfo) -> tuple[list[str], Path, dict[str, str]]:
    """Resolve the model-local runtime command, working directory, and environment."""
    if not model_info.server_command:
        raise InferenceSurfaceError(
            f"Model '{model_info.name}' does not declare server.command."
        )

    working_dir = model_info.working_dir
    command: list[str] = []
    for index, part in enumerate(model_info.server_command):
        resolved_part = part
        if index == 0:
            candidate = Path(part).expanduser()
            if not candidate.is_absolute():
                resolved_part = str(model_info.path / candidate)
        command.append(resolved_part)

    env = os.environ.copy()
    env.update(model_info.env)
    return command, working_dir, env


def _readiness_result(
    *,
    model_name: str,
    model_path: Path,
    config_path: Path,
    status: str,
    failure_class: str | None,
    action_hint: str,
    checks: list[dict[str, Any]],
    server_command: list[str] | None = None,
    runtime_command: list[str] | None = None,
    runtime_executable: str | None = None,
    working_dir: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "model": model_name,
        "model_path": str(model_path),
        "config_path": str(config_path),
        "status": status,
        "failure_class": failure_class,
        "action_hint": action_hint,
        "server_command": server_command or [],
        "runtime_command": runtime_command or [],
        "runtime_executable": runtime_executable,
        "working_dir": str(working_dir) if working_dir is not None else None,
        "env": env or {},
        "checks": checks,
    }


def _action_hint_for_failure(
    failure_class: str | None,
    *,
    model_path: Path,
) -> str:
    if failure_class is None:
        return "Ready to run predict."
    mapping = {
        "registry_missing": "Check the model name against `sure-eval models list`.",
        "config_missing": "Add a valid config.yaml under the model directory.",
        "config_invalid": "Fix config.yaml so it is valid YAML with a top-level mapping.",
        "server_command_missing": "Declare server.command in config.yaml.",
        "working_dir_missing": "Fix server.working_dir in config.yaml so it points to an existing directory.",
        "runtime_executable_missing": f"Run {model_path / 'setup.sh'} to create the model-local runtime.",
        "server_file_missing": "Add server.py or fix the configured server entrypoint.",
        "model_file_missing": "Add model.py for the model wrapper.",
        "dependency_missing": f"Run {model_path / 'setup.sh'} to install missing model-local dependencies.",
        "weights_missing": "Populate the model-local weights/cache before running predict.",
        "fixture_missing": "Fix the input fixture path so the referenced file exists.",
        "input_schema_invalid": "Fix the input JSONL to satisfy sure.inference_input.v1.",
        "output_schema_invalid": "Fix the runtime adapter output to satisfy sure.prediction.v1.",
        "runtime_launch_failed": "Inspect the model-local runtime stderr and fix launch-time failures.",
        "unsupported_runtime_protocol": "This model/task pair needs tool onboarding to define a supported runtime protocol.",
        "unsupported_task": "Predict v1 currently supports only the implemented task adapters.",
        "unsupported_task_adapter": "Predict v1 currently supports only the implemented task adapters.",
        "task_mismatch": "Use a model whose registered task matches the requested predict task.",
        "unknown_runtime_error": "Inspect stderr/logs from the model-local runtime for the root cause.",
    }
    return mapping.get(failure_class, "Inspect the model-local runtime for the root cause.")


def get_runtime_readiness(
    model_info: ModelInfo | None,
    *,
    model_name: str | None = None,
    models_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve runtime paths and report whether the model-local runtime is ready."""
    resolved_models_dir = Path(models_dir) if models_dir is not None else None

    if model_info is None:
        name = model_name or "unknown"
        model_path = (resolved_models_dir / name) if resolved_models_dir is not None else Path(name)
        config_path = model_path / "config.yaml"
        checks = [
            {
                "name": "registry_discovery",
                "passed": False,
                "details": f"Model '{name}' was not discovered by ModelRegistry.",
            }
        ]
        if not config_path.exists():
            failure_class = "config_missing" if model_path.exists() else "registry_missing"
            checks.append(
                {
                    "name": "config_file",
                    "passed": False,
                    "details": f"Missing config file: {config_path}",
                }
            )
            return _readiness_result(
                model_name=name,
                model_path=model_path,
                config_path=config_path,
                status="blocked",
                failure_class=failure_class,
                action_hint=_action_hint_for_failure(failure_class, model_path=model_path),
                checks=checks,
            )
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                config = yaml.safe_load(handle)
            if not isinstance(config, dict):
                raise TypeError(f"expected mapping, got {type(config).__name__}")
        except Exception as exc:
            checks.append(
                {
                    "name": "config_file",
                    "passed": False,
                    "details": f"Invalid config.yaml: {exc}",
                }
            )
            return _readiness_result(
                model_name=name,
                model_path=model_path,
                config_path=config_path,
                status="blocked",
                failure_class="config_invalid",
                action_hint=_action_hint_for_failure("config_invalid", model_path=model_path),
                checks=checks,
            )

        pseudo = ModelInfo(
            name=str(config.get("name", name)),
            task=str(config.get("task", "unknown")),
            path=model_path,
            config=config,
        )
        readiness = get_runtime_readiness(pseudo, model_name=name, models_dir=resolved_models_dir)
        readiness["checks"].insert(0, checks[0])
        readiness["status"] = "blocked"
        readiness["failure_class"] = readiness["failure_class"] or "registry_missing"
        readiness["action_hint"] = _action_hint_for_failure(
            readiness["failure_class"],
            model_path=model_path,
        )
        return readiness

    model_path = model_info.path
    config_path = model_path / "config.yaml"
    checks: list[dict[str, Any]] = [
        {
            "name": "registry_discovery",
            "passed": True,
            "details": f"Model '{model_info.name}' discovered by ModelRegistry.",
        }
    ]

    if not config_path.exists():
        checks.append({"name": "config_file", "passed": False, "details": f"Missing config file: {config_path}"})
        return _readiness_result(
            model_name=model_info.name,
            model_path=model_path,
            config_path=config_path,
            status="blocked",
            failure_class="config_missing",
            action_hint=_action_hint_for_failure("config_missing", model_path=model_path),
            checks=checks,
            server_command=model_info.server_command,
        )
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        if not isinstance(config, dict):
            raise TypeError(f"expected mapping, got {type(config).__name__}")
    except Exception as exc:
        checks.append({"name": "config_file", "passed": False, "details": f"Invalid config.yaml: {exc}"})
        return _readiness_result(
            model_name=model_info.name,
            model_path=model_path,
            config_path=config_path,
            status="blocked",
            failure_class="config_invalid",
            action_hint=_action_hint_for_failure("config_invalid", model_path=model_path),
            checks=checks,
            server_command=model_info.server_command,
        )
    checks.append({"name": "config_file", "passed": True, "details": str(config_path)})

    model_file = model_path / "model.py"
    model_file_exists = model_file.exists()
    checks.append({"name": "model_file", "passed": model_file_exists, "details": str(model_file)})

    server_file = model_path / "server.py"
    server_file_exists = server_file.exists()
    checks.append({"name": "server_file", "passed": server_file_exists, "details": str(server_file)})

    server_command = model_info.server_command
    if not server_command:
        checks.append({"name": "server_command", "passed": False, "details": "Missing server.command in config.yaml."})
        return _readiness_result(
            model_name=model_info.name,
            model_path=model_path,
            config_path=config_path,
            status="blocked",
            failure_class="server_command_missing",
            action_hint=_action_hint_for_failure("server_command_missing", model_path=model_path),
            checks=checks,
        )
    checks.append({"name": "server_command", "passed": True, "details": _build_command_string(server_command)})

    command, working_dir, env = _resolve_runtime_command(model_info)
    runtime_executable = command[0] if command else None
    runtime_executable_exists = bool(runtime_executable and Path(runtime_executable).expanduser().exists())
    working_dir_exists = working_dir.exists()
    checks.append({"name": "working_dir", "passed": working_dir_exists, "details": str(working_dir)})
    checks.append({"name": "runtime_executable", "passed": runtime_executable_exists, "details": str(runtime_executable)})

    failure_class: str | None = None
    if not model_file_exists:
        failure_class = "model_file_missing"
    elif not server_file_exists:
        failure_class = "server_file_missing"
    elif not working_dir_exists:
        failure_class = "working_dir_missing"
    elif not runtime_executable_exists:
        failure_class = "runtime_executable_missing"

    return _readiness_result(
        model_name=model_info.name,
        model_path=model_path,
        config_path=config_path,
        status="ready" if failure_class is None else "blocked",
        failure_class=failure_class,
        action_hint=_action_hint_for_failure(failure_class, model_path=model_path),
        checks=checks,
        server_command=server_command,
        runtime_command=command,
        runtime_executable=runtime_executable,
        working_dir=working_dir,
        env=env,
    )


def ensure_runtime_ready(model_info: ModelInfo) -> dict[str, Any]:
    """Raise a structured error when the configured model-local runtime is missing."""
    readiness = get_runtime_readiness(model_info)
    if readiness["status"] != "ready":
        failure_class = readiness["failure_class"] or "unknown_runtime_error"
        raise AdapterError(
            f"{failure_class}: {readiness['action_hint']}",
            code=failure_class,
        )
    return readiness


def _classify_exception(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(exc, SchemaValidationError):
        message = str(exc).lower()
        if "sure.prediction.v1" in message or "prediction." in message:
            return "output_schema_invalid"
        return "input_schema_invalid"
    if isinstance(exc, FileNotFoundError):
        return "fixture_missing"
    if isinstance(exc, AdapterError):
        return "unknown_runtime_error"
    if isinstance(exc, InferenceSurfaceError):
        return "unknown_runtime_error"
    return "unknown_runtime_error"


def _build_error_payload(exc: Exception) -> dict[str, Any]:
    code = _classify_exception(exc)
    return {
        "code": code,
        "message": str(exc) or code,
        "type": exc.__class__.__name__,
    }


def _classify_runtime_failure_text(details: str) -> str:
    lower = details.lower()
    if "unsupported_runtime_protocol" in lower:
        return "unsupported_runtime_protocol"
    if "unsupported_task_adapter" in lower:
        return "unsupported_task_adapter"
    if "api key" in lower or "dashscope_api_key" in lower:
        return "api_key_missing"
    if "no module named" in lower or "not installed" in lower or "cli not found" in lower:
        return "dependency_missing"
    if "audio file not found" in lower or "image not found" in lower or "fixture" in lower:
        return "fixture_missing"
    if (
        "couldn't connect" in lower
        or "cached files" in lower
        or "localentrynotfounderror" in lower
        or "from_pretrained" in lower
        or "snapshot_download" in lower
        or "download" in lower
        or "huggingface" in lower
        or "weights" in lower
    ):
        return "weights_missing"
    return "runtime_launch_failed"


def _detect_asr_wrapper_class_name(model_info: ModelInfo) -> str:
    """Infer the expected ASR wrapper class name from the model name."""
    mapping = {
        "asr_qwen3": "ASRQwen3Model",
        "asr_whisper": "ASRWhisperModel",
    }
    return mapping.get(model_info.name, "ASRModel")


def _build_prediction_record(
    *,
    model_name: str,
    task: str,
    instance_id: str,
    device: str,
    prediction: dict[str, Any] | None,
    error: dict[str, Any] | None,
    latency_ms: int | None,
) -> dict[str, Any]:
    record = {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "instance_id": instance_id,
        "model": model_name,
        "task": task,
        "status": "error" if error else "ok",
        "prediction": prediction,
        "raw_output": None,
        "runtime": {
            "latency_ms": latency_ms,
            "device": device,
        },
        "error": error,
    }
    return validate_prediction_record(record)


def _write_prediction_manifest(
    *,
    manifest_path: Path,
    model_info: ModelInfo,
    task: str,
    input_path: str | Path,
    output_path: str | Path,
    command: list[str] | None,
    server_command: list[str],
    working_dir: str | None,
    num_instances: int,
    num_ok: int,
    num_failed: int,
    status: str,
    failure_class: str | None,
    readiness_snapshot: dict[str, Any],
    started_at: str,
    finished_at: str,
) -> None:
    manifest = {
        "schema_version": PREDICTION_MANIFEST_SCHEMA_VERSION,
        "model": model_info.name,
        "task": task,
        "input_path": str(Path(input_path).expanduser().resolve()),
        "output_path": str(Path(output_path).expanduser().resolve()),
        "command": _build_command_string(command or []),
        "server_command": server_command,
        "working_dir": working_dir,
        "num_instances": num_instances,
        "num_ok": num_ok,
        "num_failed": num_failed,
        "status": status,
        "failure_class": failure_class,
        "readiness_snapshot": readiness_snapshot,
        "config_path": str((model_info.path / "config.yaml").resolve()),
        "started_at": started_at,
        "finished_at": finished_at,
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _run_asr_subprocess(
    *,
    model_info: ModelInfo,
    runtime_command: list[str],
    working_dir: Path,
    env: dict[str, str],
    record: dict[str, Any],
    device: str,
) -> dict[str, Any]:
    """Run one ASR prediction inside the model-local Python runtime."""
    if not runtime_command:
        raise InferenceSurfaceError("Resolved runtime command is empty.")

    python_executable = runtime_command[0]
    model_file = model_info.path / "model.py"
    audio_path = Path(record["input"]["audio_path"]).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    request = record.get("request") or {}
    model_path_value = model_info.env.get("MODEL_PATH") or model_info.model_id or None
    child_env = env.copy()
    child_env["DEVICE"] = device
    normalized_language = map_language_for_model(
        model_name=model_info.name,
        language=request.get("language"),
    )

    script = """
import importlib.util
import json
import pathlib
import sys

payload = json.loads(sys.stdin.read())
model_file = pathlib.Path(payload["model_file"])
spec = importlib.util.spec_from_file_location(payload["module_name"], model_file)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load model wrapper module from {model_file}")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
wrapper_cls = getattr(module, payload["wrapper_class"], None)
if wrapper_cls is None:
    for name in dir(module):
        candidate = getattr(module, name)
        if isinstance(candidate, type) and hasattr(candidate, "transcribe"):
            wrapper_cls = candidate
            break
if wrapper_cls is None:
    raise RuntimeError("unsupported_runtime_protocol: no ASR wrapper class with transcribe() found")
wrapper = wrapper_cls(
    model_path=payload["model_path"],
    device=payload["device"],
)
result = wrapper.transcribe(
    audio_path=payload["audio_path"],
    language=payload["language"],
    return_timestamps=payload["return_timestamps"],
)
output = {
    "text": result.text,
    "language": result.language or payload["language"] or "auto",
    "segments": result.timestamps or [],
    "confidence": None,
}
print(json.dumps(output, ensure_ascii=False))
""".strip()

    payload = {
        "model_file": str(model_file.resolve()),
        "module_name": f"sure_eval_predict_{model_info.name}",
        "wrapper_class": _detect_asr_wrapper_class_name(model_info),
        "model_path": model_path_value,
        "device": device,
        "audio_path": str(audio_path),
        "language": normalized_language,
        "return_timestamps": bool(request.get("timestamps", False)),
    }

    completed = subprocess.run(
        [python_executable, "-c", script],
        cwd=working_dir,
        env=child_env,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        details = stderr or stdout or f"child process exited with code {completed.returncode}"
        code = _classify_runtime_failure_text(details)
        if "unsupported_runtime_protocol:" in details:
            details = details.split("unsupported_runtime_protocol:", 1)[1].strip() or details
        raise AdapterError(details, code=code)

    stdout = completed.stdout.strip()
    if not stdout:
        raise InferenceSurfaceError("Model-local runtime produced no JSON output.")

    try:
        prediction = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise InferenceSurfaceError(
            f"Model-local runtime returned invalid JSON: {stdout}"
        ) from exc

    return prediction


def _resolve_record_path(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def _normalize_s2tt_language(value: str) -> str:
    mapping = {
        "en": "eng_Latn",
        "eng": "eng_Latn",
        "english": "eng_Latn",
        "zh": "zho_Hans",
        "zh-cn": "zho_Hans",
        "zho": "zho_Hans",
        "chinese": "zho_Hans",
    }
    return mapping.get(value.lower(), value)


def _build_tool_arguments(adapter: PredictAdapter, record: dict[str, Any]) -> dict[str, Any]:
    payload = record["input"]
    if adapter.tool_name == "process_audio":
        arguments = {
            "input_path": _resolve_record_path(payload["input_path"]),
            "output_path": _resolve_record_path(payload["output_path"]),
        }
        for field in ("sample_rate", "channels", "duration", "start_time"):
            if field in payload:
                arguments[field] = payload[field]
        return arguments
    if adapter.tool_name in {"extract_mfcc", "analyze_music_structure"}:
        return {"audio_path": _resolve_record_path(payload["audio_path"])}
    if adapter.tool_name == "vad_predict":
        arguments = {"audio_path": _resolve_record_path(payload["audio_path"])}
        if "sampling_rate" in payload:
            arguments["sampling_rate"] = payload["sampling_rate"]
        return arguments
    if adapter.tool_name in {"diarize", "diarize_with_rttm"}:
        arguments = {"audio_path": _resolve_record_path(payload["audio_path"])}
        for field in ("num_speakers", "min_speakers", "max_speakers", "output_dir"):
            if field in payload:
                value = payload[field]
                arguments[field] = _resolve_record_path(value) if field == "output_dir" else value
        return arguments
    if adapter.tool_name == "s2tt_translate":
        return {
            "audio_path": _resolve_record_path(payload["audio_path"]),
            "source_lang": _normalize_s2tt_language(payload["source_lang"]),
            "target_lang": _normalize_s2tt_language(payload["target_lang"]),
        }
    if adapter.tool_name == "speaker_verify":
        return {
            "enroll_audio": _resolve_record_path(payload["enroll_audio"]),
            "trial_audio": _resolve_record_path(payload["trial_audio"]),
        }
    if adapter.tool_name == "enhance_audio":
        return {"audio_path": _resolve_record_path(payload["audio_path"])}
    if adapter.tool_name in {"omni_chat_text_only", "omni_chat"}:
        arguments = {"message": payload["message"]}
        for field in ("generate_audio", "output_audio_path", "voice"):
            if field in payload:
                value = payload[field]
                arguments[field] = _resolve_record_path(value) if field == "output_audio_path" else value
        return arguments
    if adapter.tool_name == "describe_image":
        arguments = {"image_path": _resolve_record_path(payload["image_path"])}
        for field in ("prompt", "max_new_tokens"):
            if field in payload:
                arguments[field] = payload[field]
        return arguments
    raise AdapterError(
        f"Unsupported task adapter tool: {adapter.tool_name}",
        code="unsupported_task_adapter",
    )


def _extract_tool_prediction(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("error") is not None:
        error = response["error"]
        if isinstance(error, dict):
            message = str(error.get("message") or error)
            raise AdapterError(message, code=_classify_runtime_failure_text(message))
        message = str(error)
        raise AdapterError(message, code=_classify_runtime_failure_text(message))

    result = response.get("result")
    if not isinstance(result, dict):
        raise InferenceSurfaceError("Model-local tool response missing result object.")
    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise InferenceSurfaceError("Model-local tool response missing content.")
    first = content[0]
    if not isinstance(first, dict):
        raise InferenceSurfaceError("Model-local tool response content must be an object.")
    text = first.get("text")
    if not isinstance(text, str) or not text:
        raise InferenceSurfaceError("Model-local tool response content text must be non-empty.")
    try:
        prediction = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InferenceSurfaceError(f"Model-local tool returned invalid JSON text: {text}") from exc
    if not isinstance(prediction, dict):
        raise InferenceSurfaceError("Model-local tool JSON text must decode to an object.")
    return prediction


def _run_mcp_tool_subprocess(
    *,
    model_info: ModelInfo,
    adapter: PredictAdapter,
    runtime_command: list[str],
    working_dir: Path,
    env: dict[str, str],
    record: dict[str, Any],
) -> dict[str, Any]:
    """Run one prediction through a model-local MCP-style tools/call server."""
    if not runtime_command:
        raise InferenceSurfaceError("Resolved runtime command is empty.")
    if not adapter.tool_name:
        raise AdapterError("Task adapter is missing a tool name.", code="unsupported_task_adapter")

    child_env = env.copy()
    payload = {
        "model_dir": str(model_info.path.resolve()),
        "server_file": str((model_info.path / "server.py").resolve()),
        "module_name": f"sure_eval_predict_tool_{model_info.name}",
        "tool_name": adapter.tool_name,
        "arguments": _build_tool_arguments(adapter, record),
    }
    script = """
import importlib.util
import json
import pathlib
import sys
import types

payload = json.loads(sys.stdin.read())
model_dir = pathlib.Path(payload["model_dir"])
server_file = pathlib.Path(payload["server_file"])
package_name = payload["module_name"]
package = types.ModuleType(package_name)
package.__path__ = [str(model_dir)]
sys.modules[package_name] = package
spec = importlib.util.spec_from_file_location(f"{package_name}.server", server_file)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load model-local server module from {server_file}")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
server_cls = getattr(module, "MCPServer", None)
if server_cls is None:
    raise RuntimeError("unsupported_runtime_protocol: no MCPServer class found")
server = server_cls()
server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
response = server.handle_request({
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
        "name": payload["tool_name"],
        "arguments": payload["arguments"],
    },
})
print(json.dumps(response, ensure_ascii=False))
""".strip()

    completed = subprocess.run(
        [runtime_command[0], "-c", script],
        cwd=working_dir,
        env=child_env,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
    )
    stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip() or (
            f"child process exited with code {completed.returncode}"
        )
        raise AdapterError(details, code=_classify_runtime_failure_text(details))
    if not stdout_lines:
        details = completed.stderr.strip() or completed.stdout.strip() or "Model-local tool produced no response."
        raise InferenceSurfaceError(details)

    try:
        response = json.loads(stdout_lines[-1])
    except json.JSONDecodeError as exc:
        raise InferenceSurfaceError(
            f"Model-local tool returned invalid JSON-RPC response: {stdout_lines[-1]}"
        ) from exc
    return _extract_tool_prediction(response)


def dry_run_prediction_job(
    *,
    model_info: ModelInfo,
    input_path: str | Path,
    output_path: str | Path,
    task: str,
    device: str = "auto",
    batch_size: int = 1,
) -> dict[str, Any]:
    """Perform static predict validation without loading the model."""
    try:
        records = _read_jsonl_records(input_path, expected_task=task)
    except SchemaValidationError as exc:
        raise AdapterError(
            f"input_schema_invalid: {exc}. {_action_hint_for_failure('input_schema_invalid', model_path=model_info.path)}",
            code="input_schema_invalid",
        ) from exc
    model_file = model_info.path / "model.py"
    output_parent = Path(output_path).expanduser().resolve().parent
    if not output_parent.exists():
        raise InferenceSurfaceError(f"Output directory does not exist: {output_parent}")

    adapter = create_predict_adapter(
        model_info,
        task=task,
        device=device,
        batch_size=batch_size,
    )
    readiness = ensure_runtime_ready(model_info)

    return {
        "model": model_info.name,
        "task": task,
        "input_path": str(Path(input_path).expanduser().resolve()),
        "output_path": str(Path(output_path).expanduser().resolve()),
        "num_instances": len(records),
        "config_path": str((model_info.path / "config.yaml").resolve()),
        "model_path": str(model_file.resolve()),
        "runtime_command": _build_command_string(readiness["runtime_command"]),
        "working_dir": str(readiness["working_dir"]),
        "runtime_executable": str(readiness["runtime_executable"]),
        "env": dict(model_info.env),
        "runtime_protocol": adapter.runtime_protocol,
        "status": readiness["status"],
        "failure_class": readiness["failure_class"],
        "action_hint": readiness["action_hint"],
        "readiness_snapshot": readiness,
        "device": device,
        "batch_size": batch_size,
    }


def run_prediction_job(
    *,
    model_info: ModelInfo,
    input_path: str | Path,
    output_path: str | Path,
    task: str,
    device: str = "auto",
    batch_size: int = 1,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """Run unified prediction generation and emit JSONL + manifest."""
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = output.parent / f"{output.stem}.manifest.json"
    started_at = _utc_now()

    readiness_snapshot = get_runtime_readiness(model_info)
    server_command = readiness_snapshot["server_command"]
    working_dir_value = readiness_snapshot["working_dir"]

    try:
        records = _read_jsonl_records(input_path, expected_task=task)
    except SchemaValidationError as exc:
        finished_at = _utc_now()
        failure_class = "input_schema_invalid"
        _write_prediction_manifest(
            manifest_path=manifest_path,
            model_info=model_info,
            task=task,
            input_path=input_path,
            output_path=output,
            command=command,
            server_command=server_command,
            working_dir=working_dir_value,
            num_instances=0,
            num_ok=0,
            num_failed=0,
            status="failed",
            failure_class=failure_class,
            readiness_snapshot=readiness_snapshot,
            started_at=started_at,
            finished_at=finished_at,
        )
        raise AdapterError(
            f"{failure_class}: {exc}. {_action_hint_for_failure(failure_class, model_path=model_info.path)}",
            code=failure_class,
        ) from exc

    adapter_error: AdapterError | None = None
    adapter = None
    try:
        adapter = create_predict_adapter(
            model_info,
            task=task,
            device=device,
            batch_size=batch_size,
        )
    except AdapterError as exc:
        adapter_error = exc
    try:
        readiness = ensure_runtime_ready(model_info)
    except AdapterError as exc:
        finished_at = _utc_now()
        failure_class = exc.code if isinstance(exc.code, str) else "unknown_runtime_error"
        _write_prediction_manifest(
            manifest_path=manifest_path,
            model_info=model_info,
            task=task,
            input_path=input_path,
            output_path=output,
            command=command,
            server_command=server_command,
            working_dir=working_dir_value,
            num_instances=len(records),
            num_ok=0,
            num_failed=0,
            status="failed",
            failure_class=failure_class,
            readiness_snapshot=readiness_snapshot,
            started_at=started_at,
            finished_at=finished_at,
        )
        raise
    readiness_snapshot = readiness
    runtime_command = readiness["runtime_command"]
    working_dir = Path(readiness["working_dir"])
    env = readiness["env"]

    num_ok = 0
    num_failed = 0
    failure_class: str | None = None

    with open(output, "w", encoding="utf-8") as handle:
        for record in records:
            instance_id = record["instance_id"]
            started = perf_counter()
            try:
                if adapter_error is not None:
                    raise adapter_error
                if adapter is None:
                    raise AdapterError(
                        f"Model '{model_info.name}' does not expose a supported runtime protocol for task '{task}'.",
                        code="unsupported_runtime_protocol",
                    )
                if adapter.runtime_protocol == "python_wrapper_transcribe":
                    prediction = _run_asr_subprocess(
                        model_info=model_info,
                        runtime_command=runtime_command,
                        working_dir=working_dir,
                        env=env,
                        record=record,
                        device=device,
                    )
                elif adapter.runtime_protocol == "mcp_tool_call":
                    prediction = _run_mcp_tool_subprocess(
                        model_info=model_info,
                        adapter=adapter,
                        runtime_command=runtime_command,
                        working_dir=working_dir,
                        env=env,
                        record=record,
                    )
                else:
                    raise AdapterError(
                        f"Unsupported runtime protocol: {adapter.runtime_protocol}",
                        code="unsupported_runtime_protocol",
                    )
                latency_ms = int((perf_counter() - started) * 1000)
                output_record = _build_prediction_record(
                    model_name=model_info.name,
                    task=task,
                    instance_id=instance_id,
                    device=device,
                    prediction=prediction,
                    error=None,
                    latency_ms=latency_ms,
                )
                num_ok += 1
            except Exception as exc:
                latency_ms = int((perf_counter() - started) * 1000)
                failure_class = failure_class or _build_error_payload(exc)["code"]
                output_record = _build_prediction_record(
                    model_name=model_info.name,
                    task=task,
                    instance_id=instance_id,
                    device=device,
                    prediction=None,
                    error=_build_error_payload(exc),
                    latency_ms=latency_ms,
                )
                num_failed += 1

            handle.write(json.dumps(output_record, ensure_ascii=False) + "\n")

    finished_at = _utc_now()
    _write_prediction_manifest(
        manifest_path=manifest_path,
        model_info=model_info,
        task=task,
        input_path=input_path,
        output_path=output,
        command=command,
        server_command=server_command,
        working_dir=str(working_dir),
        num_instances=len(records),
        num_ok=num_ok,
        num_failed=num_failed,
        status="completed" if num_failed == 0 else "completed_with_errors",
        failure_class=failure_class,
        readiness_snapshot=readiness_snapshot,
        started_at=started_at,
        finished_at=finished_at,
    )

    return {
        "output_path": str(output),
        "manifest_path": str(manifest_path),
        "num_instances": len(records),
        "num_ok": num_ok,
        "num_failed": num_failed,
        "status": "completed" if num_failed == 0 else "completed_with_errors",
        "failure_class": failure_class,
        "readiness_snapshot": readiness_snapshot,
    }


def validate_prediction_artifact(
    *,
    input_path: str | Path,
    schema_name: str,
) -> dict[str, Any]:
    """Validate a unified prediction JSONL artifact."""
    return validate_prediction_file(input_path, schema_name=schema_name)
