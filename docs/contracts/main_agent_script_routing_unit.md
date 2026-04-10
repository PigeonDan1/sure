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
- `outputs`
- `completion_criteria`

## Allowed Step Types

- `prepare_dataset`
- `materialize_templates`
- `validate_execution_shell`
- `wait_for_predictions`
- `validate_predictions`
- `evaluate_predictions`
- `refresh_report`

## Must Not Do

- must not invent new script names without human approval
- must not bypass deterministic scripts for low-uncertainty work
- must not silently omit required validation before evaluation

## Wait Contract

If a route contains `wait_for_predictions`, it must follow:

- [prediction_generation_contract.md](/cpfs/user/jingpeng/workspace/sure-eval/docs/contracts/prediction_generation_contract.md)

This means `wait_for_predictions` must specify:

- where prediction files are written
- which datasets are being generated
- how completion is determined
- which status file records progress

If the final handoff surface is a one-click shell entrypoint, the route should
also define a preflight shell-validation step before formal execution.

That validation should specify:

- shell path
- bounded smoke-test mode
- expected smoke-test artifacts
- stop condition if shell validation fails

## Output Template

- [main_agent_script_routing.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_script_routing.json)
