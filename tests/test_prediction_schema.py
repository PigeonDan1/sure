from __future__ import annotations

import json

import pytest

from sure_eval.inference.errors import SchemaValidationError
from sure_eval.inference.schemas import (
    PREDICTION_SCHEMA_VERSION,
    validate_input_record,
    validate_prediction_record,
)


def test_validate_input_record_accepts_minimal_asr_row() -> None:
    row = {
        "instance_id": "case_001",
        "task": "asr",
        "input": {
            "audio_path": "tests/fixtures/shared/asr/en_16k_10s.wav",
            "sample_rate": 16000,
        },
        "request": {"language": "en"},
    }

    validated = validate_input_record(row, expected_task="asr")
    assert validated["instance_id"] == "case_001"


def test_validate_prediction_record_rejects_ok_without_text() -> None:
    row = {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "instance_id": "case_001",
        "model": "asr_qwen3",
        "task": "asr",
        "status": "ok",
        "prediction": {},
        "raw_output": None,
        "runtime": {"latency_ms": 1, "device": "auto"},
        "error": None,
    }

    with pytest.raises(SchemaValidationError, match="prediction.text"):
        validate_prediction_record(row)


def test_validate_prediction_record_accepts_error_row() -> None:
    row = {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "instance_id": "case_001",
        "model": "asr_qwen3",
        "task": "asr",
        "status": "error",
        "prediction": None,
        "raw_output": None,
        "runtime": {"latency_ms": 1, "device": "auto"},
        "error": {"code": "RuntimeError", "message": "boom"},
    }

    validated = validate_prediction_record(row)
    assert json.loads(json.dumps(validated))["status"] == "error"
