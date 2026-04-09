# Main Agent TOOL_READINESS_AND_ROUTING_UNIT Contract

## Purpose

`TOOL_READINESS_AND_ROUTING_UNIT` is the gate between task classification and main-flow execution.

Its job is to decide whether the current model should:

- be used directly through its existing server/tool harness
- receive a lightweight server smoke test first
- be handed off to the existing tool workflow

This unit exists to prevent the main-flow agent from prematurely dropping into
environment repair, wrapper-level debugging, or ad hoc model execution.

## Required Output

- `tool_readiness_state`
- `preferred_execution_path`
- `server_smoke_test_required`
- `handoff_to_tool_agent`
- `reason`
- `evidence`

## Allowed Tool Readiness States

- `server_ready`
- `server_declared_but_unverified`
- `not_tool_ready`
- `tool_broken_needs_repair`

## Allowed Preferred Execution Paths

- `direct_server_use`
- `server_smoke_test_then_use`
- `handoff_to_tool_workflow`
- `stop_and_report`

## Required Evidence Sources

The readiness decision should prefer:

- model-local `config.yaml`
- model-local `README.md`
- presence of `server.py`, `model.py`, and `.venv`
- model-local artifacts such as `verdict.json`, `validation.log`, `build.log`
- explicit user instruction

## Routing Rules

1. If the model declares a server path and has evidence of prior readiness, prefer `direct_server_use`.
2. If the model declares a server path but readiness evidence is incomplete, prefer `server_smoke_test_then_use`.
3. If server startup or minimal tool call fails due to integration or environment issues, set `handoff_to_tool_agent = true`.
4. Main-flow should not jump directly into wrapper-native or dependency-level repair before this unit finishes.

## Must Not Do

- must not select datasets yet
- must not modify tool workflow policy
- must not perform deep dependency repair
- must not bypass model-local environment conventions

## Output Template

- [main_agent_tool_readiness_routing.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_tool_readiness_routing.json)
