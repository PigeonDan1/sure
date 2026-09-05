"""
Model management for SURE-EVAL.

This module provides standardized model interfaces and registry.
"""

from __future__ import annotations

from sure_eval.models.registry import ModelRegistry, ModelInfo

__all__ = [
    "ModelRegistry",
    "ModelInfo",
]
