"""
Protocol schema definitions for SURE-EVAL.

Inspired by XFlow's declarative protocol format: standard parameter names
are defined globally, and each model declares its own parameter mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ProtocolParam:
    """A standard parameter defined by a protocol."""

    name: str
    description: str
    default: Any
    allowed_values: list[Any]
    scope: str  # "inference" | "preprocessing" | "postprocessing" | "prompt" | "compute"


@dataclass
class ProtocolConstraint:
    """A constraint rule defined by a protocol."""

    id: str
    description: str
    category: str  # "forbidden" | "required" | "allowed" | "declared"
    scope: str


@dataclass
class ProtocolDefinition:
    """Definition of a protocol (loaded from config/protocols.yaml)."""

    id: str
    name: str
    description: str
    is_default: bool
    params: dict[str, ProtocolParam]
    constraints: list[ProtocolConstraint]

    @classmethod
    def from_dict(cls, protocol_id: str, data: dict[str, Any]) -> ProtocolDefinition:
        params = {}
        for param_name, param_data in data.get("params", {}).items():
            params[param_name] = ProtocolParam(
                name=param_name,
                description=param_data.get("description", ""),
                default=param_data.get("default"),
                allowed_values=param_data.get("allowed_values", []),
                scope=param_data.get("scope", "inference"),
            )

        constraints = []
        for constraint_data in data.get("constraints", []):
            constraints.append(
                ProtocolConstraint(
                    id=constraint_data["id"],
                    description=constraint_data.get("description", ""),
                    category=constraint_data.get("category", "forbidden"),
                    scope=constraint_data.get("scope", "inference"),
                )
            )

        return cls(
            id=protocol_id,
            name=data.get("name", protocol_id),
            description=data.get("description", ""),
            is_default=data.get("is_default", False),
            params=params,
            constraints=constraints,
        )


@dataclass
class ModelParamMapping:
    """How a model maps a protocol standard param to its own API."""

    model_param: str | None  # Name of the model's own parameter, or null if unsupported
    mapping: dict[str, Any] = field(default_factory=dict)  # protocol_value -> model_value
    note: str = ""  # Human-readable explanation if unsupported


@dataclass
class ModelProtocolConfig:
    """A model's declaration for a specific protocol."""

    enabled: bool
    param_map: dict[str, ModelParamMapping]
    declared_modules: list[dict[str, str]]
    attestation: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelProtocolConfig:
        param_map = {}
        for param_name, mapping_data in data.get("param_map", {}).items():
            param_map[param_name] = ModelParamMapping(
                model_param=mapping_data.get("model_param"),
                mapping=mapping_data.get("mapping", {}),
                note=mapping_data.get("note", ""),
            )

        return cls(
            enabled=data.get("enabled", False),
            param_map=param_map,
            declared_modules=data.get("declared_modules", []),
            attestation=data.get("attestation", {}),
        )


@dataclass
class ResolvedParams:
    """Result of resolving a protocol for a specific model."""

    protocol_id: str
    standard_params: dict[str, Any]  # Protocol standard param name -> value
    model_params: dict[str, Any]  # Model actual param name -> value
    unmapped: dict[str, str]  # Protocol param name -> explanation note

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "standard_params": self.standard_params,
            "model_params": self.model_params,
            "unmapped": self.unmapped,
        }


@dataclass
class ProtocolManifest:
    """Manifest written to disk after protocol confirmation."""

    protocol_id: str
    resolved_params: dict[str, Any]
    user_confirmed: bool
    confirmed_at: str | None = None

    def to_json(self) -> str:
        import json

        return json.dumps(
            {
                "protocol_id": self.protocol_id,
                "resolved_params": self.resolved_params,
                "user_confirmed": self.user_confirmed,
                "confirmed_at": self.confirmed_at,
            },
            indent=2,
            ensure_ascii=False,
        )

    @classmethod
    def from_file(cls, path: Path) -> ProtocolManifest | None:
        import json

        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                protocol_id=data["protocol_id"],
                resolved_params=data.get("resolved_params", {}),
                user_confirmed=data.get("user_confirmed", False),
                confirmed_at=data.get("confirmed_at"),
            )
        except Exception:
            return None
