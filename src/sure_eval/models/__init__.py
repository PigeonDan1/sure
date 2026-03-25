"""
Model management for SURE-EVAL.

This module provides standardized model interfaces and registry.
"""

from __future__ import annotations

from sure_eval.models.registry import ModelRegistry, ModelInfo
from sure_eval.models.base import BaseModel, ModelConfig

__all__ = [
    "ModelRegistry",
    "ModelInfo", 
    "BaseModel",
    "ModelConfig",
]
