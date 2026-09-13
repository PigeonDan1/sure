"""Shared access to the generated SURE task capability registry."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


CAPABILITY_PATH = Path(__file__).with_name("engine-capabilities.generated.json")


@lru_cache(maxsize=1)
def capabilities() -> dict[str, Any]:
    value = json.loads(CAPABILITY_PATH.read_text(encoding="utf-8"))
    if value.get("schema") != "sure.engine_capabilities.v1":
        raise ValueError(f"Unsupported task capability schema: {value.get('schema')!r}")
    return value


def normalize_task(task: str) -> str:
    value = str(task or "").strip().lower().replace("-", "_").replace(" ", "_")
    return str(capabilities().get("aliases", {}).get(value, value)).replace("-", "_")


def canonical_tasks() -> tuple[str, ...]:
    return tuple(capabilities()["tasks"])


def accepted_tasks(*, include_suites: bool = True, include_aliases: bool = True) -> tuple[str, ...]:
    result = set(canonical_tasks())
    if include_aliases:
        result.update(capabilities().get("aliases", {}))
    if include_suites:
        result.update(capabilities().get("suites", {}))
    return tuple(sorted(result))


def task_profile(task: str) -> dict[str, Any]:
    normalized = normalize_task(task)
    try:
        return dict(capabilities()["tasks"][normalized])
    except KeyError as exc:
        raise ValueError(f"Unsupported SURE task: {task!r}") from exc


def speech_understanding_tasks() -> tuple[str, ...]:
    return tuple(capabilities()["suites"]["speech_understanding"]["membership"])


def io_contract_for_task(task: str) -> dict[str, Any]:
    profile = task_profile(task)
    contract = dict(profile["io_contract"])
    primary = str(contract["primary_field"])
    return {
        **contract,
        "output_type": "json",
        "required_fields": list(contract.get("required_fields") or [primary]),
        "nonempty_fields": list(contract.get("nonempty_fields") or [primary]),
        "json_serializable": True,
    }
