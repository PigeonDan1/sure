from __future__ import annotations

from model import ModelWrapper


def load_model(config: dict | None = None) -> ModelWrapper:
    model = ModelWrapper(config)
    model.load()
    return model
