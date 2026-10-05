#!/usr/bin/env python3
"""Task-level IO contracts shared by the SURE-TRANS adapter stages.

The task type selects a conservative default contract.  A model-specific
adapter may tighten the contract in ``model.spec.yaml`` after scaffolding, but
the defaults never assume a particular TTS codec, sample rate, or checkpoint.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - adapter images may not ship PyYAML
    yaml = None


TASK_TYPES = ("asr", "s2tt", "se", "sv", "tts", "vc")


def _audio_input_schema(*, source: bool = False, reference: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "audio_path": {"type": "string"},
        "language": {"type": "string"},
    }
    required = ["audio_path"]
    if source:
        properties["source_audio_path"] = {"type": "string"}
        required = ["source_audio_path"]
    if reference:
        properties["reference_audio_path"] = {"type": "string"}
        required.append("reference_audio_path")
    return {"type": "object", "properties": properties, "required": required}


_DEFAULTS: dict[str, dict[str, Any]] = {
    "asr": {
        "tool_name": "transcribe_audio",
        "input_schema": _audio_input_schema(),
        "io_contract": {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "text",
            "required_fields": ["text"],
            "nonempty_fields": ["text"],
            "json_serializable": True,
        },
    },
    "se": {
        "tool_name": "enhance_speech",
        "input_schema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string"},
                "output_path": {"type": "string"},
            },
            "required": ["audio_path"],
        },
        "io_contract": {
            "input_type": "audio_path",
            "output_type": "audio",
            "primary_field": "audio_path",
            "required_fields": ["audio_path"],
            "nonempty_fields": ["audio_path"],
            "json_serializable": True,
        },
    },
    "sv": {
        "tool_name": "verify_speaker",
        "input_schema": {
            "type": "object",
            "properties": {
                "enrollment_audio_path": {"type": "string"},
                "test_audio_path": {"type": "string"},
            },
            "required": ["enrollment_audio_path", "test_audio_path"],
        },
        "io_contract": {
            "input_type": "audio_pair",
            "output_type": "json",
            "primary_field": "score",
            "required_fields": ["score"],
            "nonempty_fields": [],
            "json_serializable": True,
        },
    },
    "s2tt": {
        "tool_name": "translate_audio",
        "input_schema": _audio_input_schema(),
        "io_contract": {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "text",
            "required_fields": ["text"],
            "nonempty_fields": ["text"],
            "json_serializable": True,
        },
    },
    "tts": {
        "tool_name": "synthesize_speech",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "prompt_audio_path": {"type": "string"},
                "reference_audio_path": {"type": "string"},
                "prompt_text": {"type": "string"},
                "reference_text": {"type": "string"},
                "language": {"type": "string"},
                "output_path": {"type": "string"},
            },
            "required": ["text"],
        },
        "io_contract": {
            "input_type": "text",
            "output_type": "audio",
            "primary_field": "audio_path",
            "required_fields": ["audio_path"],
            "nonempty_fields": ["audio_path"],
            "json_serializable": True,
        },
    },
    "vc": {
        "tool_name": "convert_voice",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_audio_path": {"type": "string"},
                "reference_audio_path": {"type": "string"},
                "output_path": {"type": "string"},
                "language": {"type": "string"},
            },
            "required": ["source_audio_path", "reference_audio_path"],
        },
        "io_contract": {
            "input_type": "audio_pair",
            "output_type": "audio",
            "primary_field": "audio_path",
            "required_fields": ["audio_path"],
            "nonempty_fields": ["audio_path"],
            "json_serializable": True,
        },
    },
}


def contract_for(task_type: str) -> dict[str, Any]:
    task = str(task_type).strip().lower()
    if task not in TASK_TYPES:
        raise ValueError(f"unsupported task_type={task_type!r}; expected one of {list(TASK_TYPES)}")
    return copy.deepcopy(_DEFAULTS[task])


def validate_io_contract(contract: object) -> list[str]:
    if not isinstance(contract, dict):
        return ["io_contract must be an object"]
    violations: list[str] = []
    if not isinstance(contract.get("input_type"), str) or not contract["input_type"].strip():
        violations.append("io_contract.input_type must be a non-empty string")
    if contract.get("output_type") not in {"json", "text", "audio"}:
        violations.append("io_contract.output_type must be json, text, or audio")
    primary = contract.get("primary_field")
    if not isinstance(primary, str) or not primary.strip():
        violations.append("io_contract.primary_field must be a non-empty string")
    for key in ("required_fields", "nonempty_fields"):
        value = contract.get(key)
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            violations.append(f"io_contract.{key} must be an array of non-empty strings")
    if isinstance(primary, str) and isinstance(contract.get("required_fields"), list):
        if primary not in contract["required_fields"]:
            violations.append("io_contract.primary_field must be required")
    return violations


def task_contract(task_type: str, io_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    result = contract_for(task_type)
    if io_contract is not None:
        result["io_contract"] = copy.deepcopy(io_contract)
        violations = validate_io_contract(io_contract)
        if violations:
            raise ValueError("; ".join(violations))
    return result


def load_model_spec_contract(path: str) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to read model.spec.yaml")
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("io_contract"), dict):
        raise ValueError(f"model spec has no io_contract: {path}")
    contract = value["io_contract"]
    violations = validate_io_contract(contract)
    if violations:
        raise ValueError("; ".join(violations))
    return contract
