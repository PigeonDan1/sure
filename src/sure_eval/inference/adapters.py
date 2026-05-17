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
    if requested_task in {"speaker-verification", "sv"}:
        requested_task = "speaker_verification"
    if requested_task in {"enhancement", "se"}:
        requested_task = "speech_enhancement"
    if requested_task in {"speaker_diarization", "diarization"}:
        requested_task = "sd"
    if requested_task in {"multimodal_chat", "omni_chat"}:
        requested_task = "omni"

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
        tool_names = _tool_names(model_info)
        if "extract_mfcc" in tool_names:
            tool_name = "extract_mfcc"
        elif "analyze_music_structure" in tool_names:
            tool_name = "analyze_music_structure"
        else:
            raise AdapterError(
                f"Model '{model_info.name}' does not expose a supported music_ir tool.",
                code="unsupported_task_adapter",
            )
        return PredictAdapter(
            task="music_ir",
            runtime_protocol="mcp_tool_call",
            tool_name=tool_name,
        )

    if requested_task == "vad":
        _require_model_task(model_info, requested_task=requested_task)
        _require_server_command(model_info, task=task)
        if "vad_predict" not in _tool_names(model_info):
            raise AdapterError(
                f"Model '{model_info.name}' does not expose required VAD tool 'vad_predict'.",
                code="unsupported_task_adapter",
            )
        return PredictAdapter(
            task="vad",
            runtime_protocol="mcp_tool_call",
            tool_name="vad_predict",
        )

    if requested_task == "sd":
        _require_model_task(model_info, requested_task=requested_task)
        _require_server_command(model_info, task=task)
        if "diarize" not in _tool_names(model_info):
            raise AdapterError(
                f"Model '{model_info.name}' does not expose required SD tool 'diarize'.",
                code="unsupported_task_adapter",
            )
        return PredictAdapter(
            task="sd",
            runtime_protocol="mcp_tool_call",
            tool_name="diarize",
        )

    if requested_task == "s2tt":
        _require_model_task(model_info, requested_task=requested_task)
        _require_server_command(model_info, task=task)
        if "s2tt_translate" not in _tool_names(model_info):
            raise AdapterError(
                f"Model '{model_info.name}' does not expose required S2TT tool 's2tt_translate'.",
                code="unsupported_task_adapter",
            )
        return PredictAdapter(
            task="s2tt",
            runtime_protocol="mcp_tool_call",
            tool_name="s2tt_translate",
        )

    if requested_task == "speaker_verification":
        _require_model_task(model_info, requested_task=requested_task)
        _require_server_command(model_info, task=task)
        if "speaker_verify" not in _tool_names(model_info):
            raise AdapterError(
                f"Model '{model_info.name}' does not expose required speaker verification tool 'speaker_verify'.",
                code="unsupported_task_adapter",
            )
        return PredictAdapter(
            task="speaker_verification",
            runtime_protocol="mcp_tool_call",
            tool_name="speaker_verify",
        )

    if requested_task == "speech_enhancement":
        _require_model_task(model_info, requested_task=requested_task)
        _require_server_command(model_info, task=task)
        if "enhance_audio" not in _tool_names(model_info):
            raise AdapterError(
                f"Model '{model_info.name}' does not expose required speech enhancement tool 'enhance_audio'.",
                code="unsupported_task_adapter",
            )
        return PredictAdapter(
            task="speech_enhancement",
            runtime_protocol="mcp_tool_call",
            tool_name="enhance_audio",
        )

    if requested_task == "omni":
        _require_model_task(model_info, requested_task=requested_task)
        _require_server_command(model_info, task=task)
        if "omni_chat_text_only" not in _tool_names(model_info):
            raise AdapterError(
                f"Model '{model_info.name}' does not expose required OMNI tool 'omni_chat_text_only'.",
                code="unsupported_task_adapter",
            )
        return PredictAdapter(
            task="omni",
            runtime_protocol="mcp_tool_call",
            tool_name="omni_chat_text_only",
        )

    if requested_task == "vlm":
        _require_model_task(model_info, requested_task=requested_task)
        _require_server_command(model_info, task=task)
        if "describe_image" not in _tool_names(model_info):
            raise AdapterError(
                f"Model '{model_info.name}' does not expose required VLM tool 'describe_image'.",
                code="unsupported_task_adapter",
            )
        return PredictAdapter(
            task="vlm",
            runtime_protocol="mcp_tool_call",
            tool_name="describe_image",
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
