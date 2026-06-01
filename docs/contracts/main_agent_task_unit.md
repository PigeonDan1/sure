# Main Agent TASK_CLASSIFICATION_UNIT Contract

## Purpose

`TASK_CLASSIFICATION_UNIT` is responsible for classifying the current request into one of the supported main-flow task types.

It is the first reasoning unit after intake.

## Required Output

- `task_type`
- `reason`
- `need_tool_workflow`
- `confidence`
- `input_signals`

## Allowed Task Types

- `onboarding_then_evaluate`
- `evaluate_existing_model`
- `repair_broken_model`
- `audit_results`

## Required Evidence Sources

The classification decision should prefer:

- explicit user intent
- current model state
- existing wrapper / artifact readiness
- recent failure context

## Must Not Do

- must not select datasets yet
- must not compute execution order in detail
- must not rewrite tool workflow behavior

## Output Template

- [main_agent_task_classification.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_task_classification.json)
