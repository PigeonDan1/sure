# Main Agent SCRIPT_ROUTING_UNIT Contract

## Purpose

`SCRIPT_ROUTING_UNIT` is responsible for turning main-agent decisions into an ordered deterministic execution route.

It bridges:

- task classification
- plan
- dataset decision
- deterministic scripts

## Required Output

- `steps`
- `routing_reason`
- `wait_points`
- `stop_condition`

Each step should include:

- `name`
- `script`
- `inputs`

## Allowed Step Types

- `prepare_dataset`
- `materialize_templates`
- `wait_for_predictions`
- `validate_predictions`
- `evaluate_predictions`
- `refresh_report`

## Must Not Do

- must not invent new script names without human approval
- must not bypass deterministic scripts for low-uncertainty work
- must not silently omit required validation before evaluation

## Output Template

- [main_agent_script_routing.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_script_routing.json)
