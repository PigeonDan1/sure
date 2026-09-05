"""Task-level adapter selection for the unified prediction surface."""

from __future__ import annotations

from dataclasses import dataclass

from sure_eval.inference.errors import AdapterError
from sure_eval.models.registry import ModelInfo


@dataclass(frozen=True)
class PredictAdapter:
    """Static adapter description used by the inference runner."""

    task: str
    runtime_protocol: str


def create_predict_adapter(
    model_info: ModelInfo,
    *,
    task: str,
    device: str = "auto",
    batch_size: int = 1,
) -> PredictAdapter:
    """Create a task-level adapter description for the requested predict run."""
    del device, batch_size

    requested_task = task.lower()
    model_task = model_info.task.lower()

    if requested_task != "asr":
        raise AdapterError(
            f"Unified predict v1 does not yet support task '{task}'.",
            code="unsupported_task",
        )

    if model_task != "asr":
        raise AdapterError(
            f"Model '{model_info.name}' is registered for task '{model_info.task}', not '{task}'.",
            code="task_mismatch",
        )

    model_file = model_info.path / "model.py"
    if not model_file.exists():
        raise AdapterError(
            f"Model '{model_info.name}' is missing model.py, so the ASR wrapper protocol is unavailable.",
            code="unsupported_runtime_protocol",
        )

    if not model_info.server_command:
        raise AdapterError(
            f"Model '{model_info.name}' does not declare server.command.",
            code="unsupported_runtime_protocol",
        )

    return PredictAdapter(
        task="asr",
        runtime_protocol="python_wrapper_transcribe",
    )
