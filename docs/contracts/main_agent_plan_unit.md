# Main Agent PLAN_UNIT Contract

## Purpose

`PLAN_UNIT` is responsible for producing the top-level execution plan for the current run.

## Required Output

- `goal`
- `task_type`
- `need_tool_workflow`
- `execution_steps`
- `stop_condition`
- `notes`

## Must Not Do

- must not select datasets in detail
- must not compute metrics
- must not rewrite script behavior

## Output Template

- [main_agent_plan.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_plan.json)
