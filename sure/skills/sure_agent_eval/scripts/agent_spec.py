#!/usr/bin/env python3
"""Load and validate SURE agent specs (agent.yaml).

An agent spec declares an input -> output contract plus an ordered chain of
stages; each stage names an approved model below the site policy's
``storage.approved_models_roots[0]``. The JSON Schema copy of this contract
lives in ``../schemas/agent_spec.schema.json`` — keep the two in sync.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

AGENT_REQUIRED_FIELDS = ("name", "task", "input", "output")
DEFAULT_PROMPT_TEMPLATE = "Translate to {target_language}: {text}"


class AgentSpecError(ValueError):
    """Raised when an agent spec cannot be loaded or is invalid."""


def load_agent_spec(path: Path) -> dict[str, Any]:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise AgentSpecError(f"agent spec not found: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AgentSpecError(f"agent spec is not valid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise AgentSpecError(f"agent spec must be a mapping: {path}")
    return value


def validate_agent_spec(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    agent = spec.get("agent")
    if not isinstance(agent, dict):
        return ["agent spec must carry an 'agent' mapping"]
    for field in AGENT_REQUIRED_FIELDS:
        if not str(agent.get(field) or "").strip():
            errors.append(f"agent.{field} must be a non-empty string")
    name = str(agent.get("name") or "")
    if name and not AGENT_NAME_RE.fullmatch(name):
        errors.append(f"agent.name {name!r} is not a valid identifier ({AGENT_NAME_RE.pattern})")

    stages = spec.get("stages")
    if not isinstance(stages, list) or not stages:
        errors.append("agent spec must declare a non-empty 'stages' list")
        return errors
    seen_ids: set[str] = set()
    for index, stage in enumerate(stages):
        label = f"stages[{index}]"
        if not isinstance(stage, dict):
            errors.append(f"{label} must be a mapping")
            continue
        stage_id = str(stage.get("id") or "")
        if not stage_id:
            errors.append(f"{label}.id must be a non-empty string")
        elif not STAGE_ID_RE.fullmatch(stage_id):
            errors.append(f"{label}.id {stage_id!r} is not a valid identifier ({STAGE_ID_RE.pattern})")
        elif stage_id in seen_ids:
            errors.append(f"duplicate stage id {stage_id!r}")
        else:
            seen_ids.add(stage_id)
        model = str(stage.get("model") or "")
        if not model:
            errors.append(f"{label}.model must name an approved model directory")
        elif not MODEL_ID_RE.fullmatch(model):
            errors.append(f"{label}.model {model!r} must be a bare approved model name, not a path or alias")
        prompt_template = stage.get("prompt_template")
        if prompt_template is not None and not isinstance(prompt_template, str):
            errors.append(f"{label}.prompt_template must be a string")
    return errors


def stage_mode(config: dict[str, Any]) -> str:
    """A stage model speaks MCP tools, unless its config declares the API-model pattern."""
    api = config.get("api")
    if isinstance(api, dict) and str(api.get("base_url") or "").strip():
        return "api"
    return "mcp_tool"


def render_prompt(
    template: str,
    *,
    text: str,
    target_language: str,
    source_language: str,
    dataset: str,
    key: str,
) -> str:
    return (template or DEFAULT_PROMPT_TEMPLATE).format(
        text=text,
        target_language=target_language,
        source_language=source_language,
        dataset=dataset,
        key=key,
    )
