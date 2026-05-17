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
    tool_name: str | None = None


def _tool_names(model_info: ModelInfo) -> set[str]:
    tools = model_info.config.get("tools", [])
    if not isinstance(tools, list):
        return set()
    return {str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name")}


def _require_model_task(model_info: ModelInfo, *, requested_task: str) -> None:
    model_task = model_info.task.lower()
    if model_task != requested_task:
        raise AdapterError(
            f"Model '{model_info.name}' is registered for task '{model_info.task}', not '{requested_task}'.",
            code="task_mismatch",
        )


def _require_server_command(model_info: ModelInfo, *, task: str) -> None:
    if not model_info.server_command:
        raise AdapterError(
            f"Model '{model_info.name}' does not declare server.command for task '{task}'.",
            code="unsupported_runtime_protocol",
        )


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

    if requested_task == "utility":
        _require_model_task(model_info, requested_task=requested_task)
        _require_server_command(model_info, task=task)
        if "process_audio" not in _tool_names(model_info):
            raise AdapterError(
                f"Model '{model_info.name}' does not expose required utility tool 'process_audio'.",
                code="unsupported_task_adapter",
            )
        return PredictAdapter(
            task="utility",
            runtime_protocol="mcp_tool_call",
            tool_name="process_audio",
        )

    if requested_task == "music_ir":
        _require_model_task(model_info, requested_task=requested_task)
        _require_server_command(model_info, task=task)
        if "extract_mfcc" not in _tool_names(model_info):
            raise AdapterError(
                f"Model '{model_info.name}' does not expose supported music_ir tool 'extract_mfcc'.",
                code="unsupported_task_adapter",
            )
        return PredictAdapter(
            task="music_ir",
            runtime_protocol="mcp_tool_call",
            tool_name="extract_mfcc",
        )

    if requested_task != "asr":
        raise AdapterError(
            f"Unified predict v1 does not yet support task '{task}'.",
            code="unsupported_task_adapter",
        )

    _require_model_task(model_info, requested_task="asr")

    model_file = model_info.path / "model.py"
    if not model_file.exists():
        raise AdapterError(
            f"Model '{model_info.name}' is missing model.py, so the ASR wrapper protocol is unavailable.",
            code="unsupported_runtime_protocol",
        )

    _require_server_command(model_info, task=task)

    return PredictAdapter(
        task="asr",
        runtime_protocol="python_wrapper_transcribe",
    )
