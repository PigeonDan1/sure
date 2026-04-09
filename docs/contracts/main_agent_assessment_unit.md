# Main Agent ASSESSMENT_UNIT Contract

## Purpose

`ASSESSMENT_UNIT` is responsible for interpreting script outputs and deciding the final state of the current run.

## Required Output

- `status`
- `evidence`
- `blocking_issues`
- `next_action`

## Allowed Status Values

- `success`
- `partial_success`
- `blocked`
- `needs_tool_workflow`
- `needs_human_input`

## Must Not Do

- must not silently retry without explanation
- must not hide missing evidence

## Output Template

- [main_agent_assessment_report.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_assessment_report.json)
