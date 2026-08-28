from __future__ import annotations


class ModelWrapper:
    def __init__(self, config=None):
        self.config = config or {}
        self.model = None

    def load(self) -> None:
        raise NotImplementedError("Replace with the model-specific persistent loader")

    def predict(self, input_data):
        raise NotImplementedError("Adapt the original inference entrypoint and return JSON-serializable output")

    def healthcheck(self) -> dict:
        return {"status": "ready" if self.model is not None else "loading", "model_loaded": self.model is not None}
