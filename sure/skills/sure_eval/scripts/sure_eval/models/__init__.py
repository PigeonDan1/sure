"""
Model management for SURE-EVAL.

This module provides standardized model interfaces and registry.
"""

from __future__ import annotations

from sure_eval.models.registry import ModelRegistry, ModelInfo
from sure_eval.models.base import BaseModel, ModelConfig
from sure_eval.models.model_mapping import (
    get_benchmark_name,
    get_tool_name,
    get_model_metadata,
    is_sota,
    list_mapped_tools,
    list_benchmark_models,
    register_mapping,
    TOOL_TO_BENCHMARK,
    BENCHMARK_TO_TOOL,
)

__all__ = [
    "ModelRegistry",
    "ModelInfo", 
    "BaseModel",
    "ModelConfig",
    "get_benchmark_name",
    "get_tool_name",
    "get_model_metadata",
    "is_sota",
    "list_mapped_tools",
    "list_benchmark_models",
    "register_mapping",
    "TOOL_TO_BENCHMARK",
    "BENCHMARK_TO_TOOL",
]
