from __future__ import annotations

from model import ModelWrapper


def create_model(config: dict | None = None) -> ModelWrapper:
    return ModelWrapper(config)
