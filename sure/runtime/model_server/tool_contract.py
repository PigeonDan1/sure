#!/usr/bin/env python3
"""The tool contract a model's `config.yaml` must state for its server to be callable.

`/sure_infer` filters the arguments it sends against the selected tool's
`input_schema.properties`, and refuses a tool that declares none -- filtering
against an empty schema would send the server no arguments at all, `audio_path`
included, and nothing would notice. That refusal is correct, but it fires at
inference time, after the model has already been packaged and approved.

So the same rule is applied earlier and by every hand that touches the bundle:
the onboarding gate and the approval re-check both read it from here, and the
client that finally calls the tool reads the same fields. A `config.yaml` with
`tools: [{name: vad}]` and nothing else describes a tool no client can call, and
this is where that is said out loud instead of at the end of the pipeline.

Pure functions over a parsed `config.yaml`; nothing here starts a process or
touches the filesystem.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


class ToolContractError(ValueError):
    """`config.yaml` does not declare a tool contract a client can call."""


@dataclass(frozen=True)
class ToolContract:
    """One tool's callable contract: its name and the schema arguments filter against."""

    name: str
    input_schema: dict[str, Any]

    @property
    def properties(self) -> dict[str, Any]:
        return self.input_schema["properties"]

    @property
    def required(self) -> list[str]:
        return [str(name) for name in self.input_schema.get("required") or []]

    @property
    def allowed(self) -> set[str]:
        return {str(key) for key in self.properties}

    def digest(self) -> str:
        """A stable fingerprint of the callable contract, for sealing evidence."""
        payload = {
            "name": self.name,
            "properties": sorted(str(key) for key in self.properties),
            "required": sorted(self.required),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def essential(self) -> dict[str, Any]:
        """The part of the schema that decides which arguments are sent.

        A server's `tools/list` may decorate its schema with titles and
        descriptions the config does not carry; those do not change what a client
        may send, so comparing contracts must ignore them.
        """
        return {
            "type": str(self.input_schema.get("type") or "object"),
            "properties": sorted(str(key) for key in self.properties),
            "required": sorted(self.required),
        }


def _schema_of(tool_name: str, tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        raise ToolContractError(
            f"tool {tool_name!r} declares no input_schema mapping; a client cannot know "
            "which arguments to send it"
        )
    declared_type = schema.get("type")
    # An omitted `type` describes an object just as well as a stated one, and
    # plenty of working configs omit it; only a contradicting type is a defect.
    if declared_type is not None and declared_type != "object":
        raise ToolContractError(
            f"tool {tool_name!r} declares input_schema.type {declared_type!r}; a tool "
            "schema must describe the object of arguments it accepts"
        )
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ToolContractError(
            f"selected tool {tool_name!r} declares no input_schema.properties; "
            "arguments can only be filtered against a declared schema"
        )
    required = schema.get("required")
    if required is not None and not isinstance(required, list):
        raise ToolContractError(f"tool {tool_name!r} declares input_schema.required that is not a list")
    for name in required or []:
        if not isinstance(name, str) or not name:
            raise ToolContractError(f"tool {tool_name!r} declares a non-string input_schema.required entry")
        if name not in properties:
            raise ToolContractError(
                f"tool {tool_name!r} requires {name!r}, which input_schema.properties does not declare"
            )
    return schema


def tool_contracts(config: Any, *, source: str = "config.yaml") -> list[ToolContract]:
    """Every tool the config declares, or raise on the first unusable one.

    Raises rather than returning whatever it could read: a bundle whose tool list
    is ambiguous is exactly the bundle that should not reach approval.
    """
    if not isinstance(config, dict):
        raise ToolContractError(f"{source} must be a mapping")
    tools = config.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ToolContractError(f"{source} declares no tools[] to call")
    contracts: list[ToolContract] = []
    seen: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            raise ToolContractError(f"{source} declares a tools[] entry that is not a mapping")
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise ToolContractError(f"{source} declares a tool with no name")
        if name in seen:
            raise ToolContractError(f"{source} declares tool {name!r} more than once")
        seen.add(name)
        contracts.append(ToolContract(name=name, input_schema=_schema_of(name, tool)))
    return contracts


def select_tool(config: Any, tool_name: str, *, source: str = "config.yaml") -> ToolContract:
    """The named tool's contract, or raise listing what the config does declare."""
    contracts = tool_contracts(config, source=source)
    for contract in contracts:
        if contract.name == tool_name:
            return contract
    raise ToolContractError(
        f"selected tool {tool_name!r} is not declared in {source}; "
        f"declared tools: {sorted(contract.name for contract in contracts)}"
    )
