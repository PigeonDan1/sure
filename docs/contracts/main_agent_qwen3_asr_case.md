# Main Agent Case: `qwen3_asr`

## Purpose

This document records the first real replay case for the main-agent flow.

It exists to answer one practical question:

Can the current main-agent flow route a real model correctly, including failure,
handoff, repair, and resumed progress?

For this case, the answer is **yes**.

## Model

- Model: `src/sure_eval/models/asr_qwen3`
- Task: ASR
- Tool path: model-local MCP server via `config.yaml`

## What Happened

### Phase 1: Incorrect legacy behavior

In the first attempt, execution dropped too early into wrapper-level and
dependency-level debugging.

This was the wrong behavior for main flow because it bypassed the intended
tool-first routing logic.

### Phase 2: Flow correction

A new unit was introduced:

- `TOOL_READINESS_AND_ROUTING_UNIT`

This unit was inserted between:

- `TASK_CLASSIFICATION_UNIT`
- `PLAN_UNIT`

Its job is to decide whether the model is:

- `server_ready`
- `server_declared_but_unverified`
- `not_tool_ready`
- `tool_broken_needs_repair`

and whether main flow should:

- use the server directly
- do a server smoke test first
- hand off to the tool workflow

### Phase 3: Strict replay before repair

After the flow correction, a strict replay was run.

Observed behavior:

- `initialize` succeeded
- `tools/list` succeeded
- `tools/call` failed

At that time, the failure was runtime-related, so the correct decision was:

- `tool_readiness_state = tool_broken_needs_repair`
- `preferred_execution_path = handoff_to_tool_workflow`

This was the correct main-agent behavior.

### Phase 4: Tool repair

The `qwen3_asr` tool path was then repaired in model-local code.

Key fixes:

- added `torchvision` compatibility fallback
- switched path-based audio loading to a more stable `soundfile + numpy` path
- preferred local Hugging Face snapshot resolution before remote lookup

These fixes were applied to:

- [model.py](/cpfs/user/jingpeng/workspace/sure-eval/src/sure_eval/models/asr_qwen3/model.py)

### Phase 5: Strict replay after repair

After repair, the same server-first replay was run again.

Observed behavior:

- `initialize` succeeded
- `tools/list` succeeded
- `tools/call` succeeded

Real returned text:

`甚至出现交易几乎停滞的情况。`

The correct decision then became:

- `tool_readiness_state = server_ready`
- `preferred_execution_path = direct_server_use`

Main flow then continued to:

- `DATASET_SCOPE_UNIT`
- `SCRIPT_ROUTING_UNIT`
- `prepare_sure_dataset.py`
- `materialize_predictions_template.py`

and stopped at the expected prediction-generation wait point.

## Why This Case Matters

This case shows that the current main-agent flow can:

1. stop at the correct boundary when a declared tool path is still broken
2. hand off to tool repair instead of improvising main-flow fixes
3. resume correctly after repair
4. continue into dataset and script routing without redesign

That means the flow is not only valid in the success case.
It is also valid under realistic failure and recovery.

## Current Verdict

For `qwen3_asr`, the current main-agent flow is:

- **routing-correct**
- **handoff-capable**
- **recovery-capable**
- **ready to continue into prediction generation**

## Remaining Boundary

This case did **not** complete full-dataset prediction generation and scoring.

That remaining work belongs to the heavy execution stage:

- generate predictions
- validate predictions
- evaluate predictions

So the unresolved part is now execution volume, not routing design.

## Related Run Artifacts

Blocked replay before repair:

- [/tmp/main_agent_strict_replay_asr_qwen3_001/task_classification.json](/tmp/main_agent_strict_replay_asr_qwen3_001/task_classification.json)
- [/tmp/main_agent_strict_replay_asr_qwen3_001/tool_readiness_routing.json](/tmp/main_agent_strict_replay_asr_qwen3_001/tool_readiness_routing.json)
- [/tmp/main_agent_strict_replay_asr_qwen3_001/assessment_report.json](/tmp/main_agent_strict_replay_asr_qwen3_001/assessment_report.json)
- [/tmp/main_agent_strict_replay_asr_qwen3_001/main_agent_run_report.json](/tmp/main_agent_strict_replay_asr_qwen3_001/main_agent_run_report.json)

Successful replay after repair:

- [/tmp/main_agent_strict_replay_asr_qwen3_002/task_classification.json](/tmp/main_agent_strict_replay_asr_qwen3_002/task_classification.json)
- [/tmp/main_agent_strict_replay_asr_qwen3_002/tool_readiness_routing.json](/tmp/main_agent_strict_replay_asr_qwen3_002/tool_readiness_routing.json)
- [/tmp/main_agent_strict_replay_asr_qwen3_002/dataset_decision.json](/tmp/main_agent_strict_replay_asr_qwen3_002/dataset_decision.json)
- [/tmp/main_agent_strict_replay_asr_qwen3_002/script_routing.json](/tmp/main_agent_strict_replay_asr_qwen3_002/script_routing.json)
- [/tmp/main_agent_strict_replay_asr_qwen3_002/assessment_report.json](/tmp/main_agent_strict_replay_asr_qwen3_002/assessment_report.json)
- [/tmp/main_agent_strict_replay_asr_qwen3_002/main_agent_run_report.json](/tmp/main_agent_strict_replay_asr_qwen3_002/main_agent_run_report.json)
