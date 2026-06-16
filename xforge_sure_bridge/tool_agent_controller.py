from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ToolAgentControllerError(ValueError):
    """Raised when the deterministic SURE model tool-agent controller cannot proceed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ToolAgentControllerError(f"{path} must contain a JSON object")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def accept_tool_agent_handoff(tool_agent_request_path: str | Path) -> dict[str, Any]:
    """Consume XForge handoff and advance deterministic SURE tool-agent states.

    This is not an LLM replacement for model-specific wrapper work. It is the
    deterministic controller boundary that verifies XForge outputs, records the
    tool-agent state, and stops at the next required human gate.
    """

    request_path = Path(tool_agent_request_path).resolve()
    request = _load_json(request_path)
    model_dir = Path(str(request["model_dir"])).resolve()
    artifacts_dir = model_dir / "artifacts"

    required_inputs = {
        "model_spec": request["inputs"].get("model_spec"),
        "backend_choice": request["inputs"].get("backend_choice"),
        "build_plan": request["inputs"].get("build_plan"),
        "spec_validation": request["inputs"].get("spec_validation"),
        "preflight_summary": request["inputs"].get("preflight_summary"),
        "local_uv_env": request["inputs"].get("local_uv_env"),
    }
    missing_inputs = [
        name for name, value in required_inputs.items() if not value or not Path(str(value)).exists()
    ]
    if missing_inputs:
        raise ToolAgentControllerError("missing tool-agent input(s): " + ", ".join(missing_inputs))
    weights_manifest = request["inputs"].get("weights_manifest")
    weights_manifest_path = Path(str(weights_manifest)).resolve() if weights_manifest else None
    has_weights = weights_manifest_path is not None and weights_manifest_path.exists()

    spec_validation = _load_json(Path(str(required_inputs["spec_validation"])))
    local_uv_env = _load_json(Path(str(required_inputs["local_uv_env"])))
    build_plan = _load_json(Path(str(required_inputs["build_plan"])))

    completed_states: list[str] = []
    blocking_issues: list[str] = []
    if spec_validation.get("status") == "passed":
        completed_states.append("VALIDATE_SPEC")
    else:
        blocking_issues.append("spec_validation_not_passed")

    if local_uv_env.get("status") == "ready":
        completed_states.append("LOCAL_UV_BOOTSTRAP")
    else:
        blocking_issues.append("local_uv_not_ready")

    if has_weights:
        completed_states.append("FETCH_WEIGHTS")
        current_state = "DOCKER_BUILD_CONFIRM"
        status = "blocked_for_user_confirmation" if not blocking_issues else "blocked_for_repair"
        pending_interaction = request.get("pending_user_interaction") or {
            "state": "DOCKER_BUILD_CONFIRM",
            "continues_to_state": "BUILD_ENV",
            "question": "Confirm Docker build plan before entering BUILD_ENV.",
        }
    else:
        current_state = "FETCH_WEIGHTS"
        status = "blocked_for_weights_fetch" if not blocking_issues else "blocked_for_repair"
        pending_interaction = {
            "state": "FETCH_WEIGHTS",
            "continues_to_state": "DOCKER_BUILD_CONFIRM",
            "question": "Rerun XForge ModelScope fetch without --no-download to materialize checkpoints before Docker confirmation.",
        }

    generated_at = _utc_now()
    state = {
        "agent": "sure_model_tool_agent",
        "controller": "xforge_sure_bridge.tool_agent_controller",
        "status": status,
        "current_state": current_state,
        "completed_states": completed_states,
        "blocking_issues": blocking_issues,
        "target_agent_contract": request.get("target_agent_contract"),
        "model_dir": str(model_dir),
        "handoff_consumed": str(request_path),
        "pending_user_interaction": pending_interaction,
        "build_env": {
            "status": "blocked_until_user_confirms_docker_build",
            "command": (build_plan.get("docker") or {}).get("build_command"),
        },
        "weights_manifest": str(weights_manifest_path) if has_weights else None,
        "updated_at": generated_at,
    }
    state_path = artifacts_dir / "tool_agent_state.json"
    _write_json(state_path, state)

    run_report = {
        "agent": "sure_model_tool_agent",
        "controller": "xforge_sure_bridge.tool_agent_controller",
        "status": status,
        "handoff_consumed": str(request_path),
        "model_dir": str(model_dir),
        "completed_states": completed_states,
        "current_state": current_state,
        "blocking_issues": blocking_issues,
        "blocking_user_interaction": pending_interaction
        if status in {"blocked_for_user_confirmation", "blocked_for_weights_fetch"}
        else None,
        "artifacts": {
            "tool_agent_state": str(state_path.resolve()),
            "build_plan": str(Path(str(required_inputs["build_plan"])).resolve()),
            "spec_validation": str(Path(str(required_inputs["spec_validation"])).resolve()),
            "local_uv_env": str(Path(str(required_inputs["local_uv_env"])).resolve()),
            "weights_manifest": str(weights_manifest_path) if has_weights else None,
        },
        "created_at": generated_at,
    }
    run_report_path = artifacts_dir / "tool_agent_run_report.json"
    _write_json(run_report_path, run_report)

    return {
        "status": status,
        "current_state": current_state,
        "completed_states": completed_states,
        "state_path": str(state_path.resolve()),
        "run_report_path": str(run_report_path.resolve()),
        "requires_user_confirmation_before_build": status == "blocked_for_user_confirmation",
    }
