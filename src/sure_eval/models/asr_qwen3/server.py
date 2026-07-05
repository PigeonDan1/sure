from __future__ import annotations

from model import ModelWrapper


def create_model() -> ModelWrapper:
    model = ModelWrapper()
    model.load()
    return model


if __name__ == "__main__":
    model = create_model()
    print(model.health())
