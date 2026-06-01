# Main Agent DATASET_SCOPE_UNIT Contract

## Purpose

`DATASET_SCOPE_UNIT` is responsible for deciding which canonical datasets should be evaluated for the current model or task.

## Required Output

- `selected_datasets`
- `skipped_datasets`
- `selection_basis`

Each skipped dataset must include a reason.

## Must Use

- model README
- existing tool artifacts
- explicit human constraints

## Must Not Do

- must not guess capability from task name alone
- must not launch scoring directly

## Output Template

- [main_agent_dataset_decision.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_dataset_decision.json)
