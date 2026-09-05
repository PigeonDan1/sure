"""
Protocol parameter resolver.

Maps protocol standard parameters to model-specific inference parameters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from sure_eval.models.registry import ModelInfo

from .schema import (
    ModelParamMapping,
    ModelProtocolConfig,
    ProtocolDefinition,
    ProtocolParam,
    ResolvedParams,
)


class ProtocolResolver:
    """Resolves protocol standard parameters to model-specific parameters."""

    def __init__(self, protocols_path: str | Path | None = None) -> None:
        if protocols_path is None:
            protocols_path = (
                Path(__file__).parent.parent.parent.parent / "config" / "protocols.yaml"
            )
        self.protocols_path = Path(protocols_path)
        self._protocols: dict[str, ProtocolDefinition] | None = None

    def _load_protocols(self) -> dict[str, ProtocolDefinition]:
        if self._protocols is not None:
            return self._protocols

        with open(self.protocols_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        protocols = {}
        for protocol_id, protocol_data in data.get("protocols", {}).items():
            protocols[protocol_id] = ProtocolDefinition.from_dict(protocol_id, protocol_data)

        self._protocols = protocols
        return protocols

    def get_protocol(self, protocol_id: str) -> ProtocolDefinition | None:
        """Get a protocol definition by ID."""
        protocols = self._load_protocols()
        return protocols.get(protocol_id)

    def list_protocols(self) -> list[str]:
        """List all available protocol IDs."""
        protocols = self._load_protocols()
        return list(protocols.keys())

    def get_default_protocol(self) -> str:
        """Get the default protocol ID."""
        protocols = self._load_protocols()
        for protocol_id, protocol in protocols.items():
            if protocol.is_default:
                return protocol_id
        # Keep future onboard-generated protocol declarations aligned with eval.
        return "standard_system"

    def resolve(
        self,
        protocol_id: str,
        model_info: ModelInfo,
    ) -> ResolvedParams:
        """
        Resolve protocol parameters for a given model.

        Steps:
        1. Load protocol definition
        2. Load model's protocol parameter map
        3. Map each protocol standard param to model param
        4. Return resolved params + unmapped notes
        """
        protocol = self.get_protocol(protocol_id)
        if protocol is None:
            raise ValueError(f"Unknown protocol: {protocol_id}")

        # Load model protocol config
        model_protocol_config = self._load_model_protocol_config(model_info, protocol_id)

        standard_params: dict[str, Any] = {}
        model_params: dict[str, Any] = {}
        unmapped: dict[str, str] = {}

        for param_name, protocol_param in protocol.params.items():
            # Standard param value
            standard_params[param_name] = protocol_param.default

            # Check if model has a mapping for this param
            mapping = model_protocol_config.param_map.get(param_name)
            if mapping is None:
                # Model didn't declare this param at all
                unmapped[param_name] = f"Model '{model_info.name}' did not declare mapping for '{param_name}'"
                continue

            if mapping.model_param is None:
                # Model explicitly declared this param as unsupported
                note = mapping.note or f"Model '{model_info.name}' does not support '{param_name}'"
                unmapped[param_name] = note
                continue

            # Map protocol value to model value
            protocol_value = protocol_param.default
            model_value = mapping.mapping.get(str(protocol_value), protocol_value)

            # Special handling for precision -> dtype mapping
            if param_name == "precision" and mapping.model_param == "dtype":
                model_value = self._map_precision_to_dtype(str(protocol_value))

            model_params[mapping.model_param] = model_value

        return ResolvedParams(
            protocol_id=protocol_id,
            standard_params=standard_params,
            model_params=model_params,
            unmapped=unmapped,
        )

    def _load_model_protocol_config(
        self, model_info: ModelInfo, protocol_id: str
    ) -> ModelProtocolConfig:
        """Load a model's protocol configuration."""
        protocols_data = model_info.config.get("protocols", {})
        protocol_data = protocols_data.get(protocol_id, {})

        if not protocol_data:
            # Model didn't declare this protocol — return empty config
            return ModelProtocolConfig(
                enabled=False,
                param_map={},
                declared_modules=[],
                attestation={},
            )

        return ModelProtocolConfig.from_dict(protocol_data)

    @staticmethod
    def _map_precision_to_dtype(precision: str) -> str:
        """Map precision string to PyTorch dtype string."""
        mapping = {
            "float16": "torch.float16",
            "float32": "torch.float32",
            "bfloat16": "torch.bfloat16",
        }
        return mapping.get(precision, "torch.float16")
