# Main Agent RUN_REPORT_UNIT Contract

## Purpose

`RUN_REPORT_UNIT` is responsible for summarizing the full run into one structured report.

It is the terminal aggregation layer for the main flow harness.

## Required Output

- `task_type`
- `goal`
- `selected_datasets`
- `executed_steps`
- `status`
- `artifacts`
- `next_action`

## Allowed Status Values

- `success`
- `partial_success`
- `blocked`
- `needs_tool_workflow`
- `needs_human_input`

## Aggregation Rule

The run report must summarize upstream unit outputs rather than replace them.

It should point to:

- task classification artifact
- plan artifact
- dataset decision artifact
- script routing artifact
- assessment artifact

## Must Not Do

- must not drop blocking issues from the assessment layer
- must not report success if upstream assessment is blocked

## Output Template

- [main_agent_run_report.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_run_report.json)
