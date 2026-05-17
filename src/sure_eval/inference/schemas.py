"""Schema validation helpers for unified inference artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sure_eval.inference.errors import SchemaValidationError

INPUT_SCHEMA_VERSION = "sure.inference_input.v1"
PREDICTION_SCHEMA_VERSION = "sure.prediction.v1"
PREDICTION_MANIFEST_SCHEMA_VERSION = "sure.prediction_manifest.v1"


def _ensure_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaValidationError(f"Field '{field}' must be an object.")
    return value


def _ensure_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise SchemaValidationError(f"Field '{field}' must be a string.")
    return value


def _ensure_integer(value: Any, *, field: str) -> int:
    if not isinstance(value, int):
        raise SchemaValidationError(f"Field '{field}' must be an integer.")
    return value


def _ensure_number(value: Any, *, field: str) -> int | float:
    if not isinstance(value, (int, float)):
        raise SchemaValidationError(f"Field '{field}' must be a number.")
    return value


def ensure_json_serializable(value: Any) -> None:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(f"Value is not JSON serializable: {exc}") from exc


def validate_input_record(
    record: Any,
    *,
    expected_task: str | None = None,
) -> dict[str, Any]:
    """Validate one inference input record."""
    row = _ensure_mapping(record, field="record")

    instance_id = _ensure_string(row.get("instance_id"), field="instance_id")
    if not instance_id:
        raise SchemaValidationError("Field 'instance_id' must not be empty.")

    task = _ensure_string(row.get("task"), field="task")
    if expected_task and task.lower() != expected_task.lower():
        raise SchemaValidationError(
            f"Field 'task' must be '{expected_task}', got '{task}'."
        )

    input_payload = _ensure_mapping(row.get("input"), field="input")
    task_key = task.lower()
    if task_key == "asr":
        audio_path = _ensure_string(input_payload.get("audio_path"), field="input.audio_path")
        if not audio_path:
            raise SchemaValidationError("Field 'input.audio_path' must not be empty.")

        sample_rate = _ensure_integer(input_payload.get("sample_rate"), field="input.sample_rate")
        if sample_rate <= 0:
            raise SchemaValidationError("Field 'input.sample_rate' must be positive.")
    elif task_key == "utility":
        input_path = _ensure_string(input_payload.get("input_path"), field="input.input_path")
        output_path = _ensure_string(input_payload.get("output_path"), field="input.output_path")
        if not input_path:
            raise SchemaValidationError("Field 'input.input_path' must not be empty.")
        if not output_path:
            raise SchemaValidationError("Field 'input.output_path' must not be empty.")
        for field in ("sample_rate", "channels"):
            if field in input_payload:
                value = _ensure_integer(input_payload.get(field), field=f"input.{field}")
                if value <= 0:
                    raise SchemaValidationError(f"Field 'input.{field}' must be positive.")
        for field in ("duration", "start_time"):
            if field in input_payload:
                _ensure_number(input_payload.get(field), field=f"input.{field}")
    elif task_key == "music_ir":
        audio_path = _ensure_string(input_payload.get("audio_path"), field="input.audio_path")
        if not audio_path:
            raise SchemaValidationError("Field 'input.audio_path' must not be empty.")

    request = row.get("request", {})
    if request is None:
        request = {}
    _ensure_mapping(request, field="request")

    ensure_json_serializable(row)
    return row


def validate_prediction_record(record: Any) -> dict[str, Any]:
    """Validate one unified prediction record."""
    row = _ensure_mapping(record, field="record")
    required_fields = [
        "schema_version",
        "instance_id",
        "model",
        "task",
        "status",
        "prediction",
        "error",
    ]
    for field in required_fields:
        if field not in row:
            raise SchemaValidationError(f"Missing required field '{field}'.")

    if row["schema_version"] != PREDICTION_SCHEMA_VERSION:
        raise SchemaValidationError(
            f"Field 'schema_version' must be '{PREDICTION_SCHEMA_VERSION}'."
        )

    _ensure_string(row["instance_id"], field="instance_id")
    _ensure_string(row["model"], field="model")
    _ensure_string(row["task"], field="task")

    status = row["status"]
    if status not in {"ok", "error"}:
        raise SchemaValidationError("Field 'status' must be 'ok' or 'error'.")

    if status == "ok":
        if row["error"] is not None:
            raise SchemaValidationError("Field 'error' must be null when status='ok'.")
        prediction = _ensure_mapping(row["prediction"], field="prediction")
        if row["task"].lower() == "asr":
            _ensure_string(prediction.get("text"), field="prediction.text")
    else:
        error = _ensure_mapping(row["error"], field="error")
        _ensure_string(error.get("code"), field="error.code")
        _ensure_string(error.get("message"), field="error.message")

    ensure_json_serializable(row)
    return row


def validate_prediction_file(
    input_path: str | Path,
    *,
    schema_name: str,
) -> dict[str, Any]:
    """Validate a prediction JSONL file and return a summary."""
    if schema_name != PREDICTION_SCHEMA_VERSION:
        raise SchemaValidationError(f"Unsupported schema '{schema_name}'.")

    path = Path(input_path)
    if not path.exists():
        raise SchemaValidationError(f"Prediction file not found: {path}")

    num_records = 0
    num_ok = 0
    num_error = 0

    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                raise SchemaValidationError(
                    f"Line {line_number} in {path} is empty."
                )

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaValidationError(
                    f"Line {line_number} in {path} is not valid JSON: {exc}"
                ) from exc

            validate_prediction_record(record)
            num_records += 1
            if record["status"] == "ok":
                num_ok += 1
            else:
                num_error += 1

    return {
        "schema": schema_name,
        "input_path": str(path),
        "num_records": num_records,
        "num_ok": num_ok,
        "num_error": num_error,
    }
