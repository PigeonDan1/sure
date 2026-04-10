# Main Agent EXECUTION_READINESS_UNIT Contract

## Purpose

`EXECUTION_READINESS_UNIT` is the final preflight gate before a human launches
the generated shell in the background.

Its purpose is to make shell validation part of the main-agent flow, so users
do not discover shell/runtime issues only after starting a full one-click run.

## Required Output

- `execution_ready`
- `status`
- `validation_mode`
- `validated_shell_entrypoint`
- `smoke_test_command`
- `smoke_test_run_dir`
- `checked_artifacts`
- `blocking_issues`
- `next_action`

## Validation Modes

Allowed values:

- `syntax_only`
- `smoke_test`
- `smoke_test_with_prediction_generation`

For single-model single-dataset shells, the recommended default is:

- `smoke_test_with_prediction_generation`

## Required Checks

A compliant execution-readiness validation should check:

1. the shell entrypoint exists
2. the shell passes `bash -n`
3. the shell supports a bounded smoke mode
4. the smoke mode can create a valid model-local run directory
5. the smoke mode can write required run evidence
6. prediction generation can at least begin under the approved execution path

## Required Evidence

This unit should leave evidence for:

- shell path
- smoke-test command
- smoke-test mode
- run directory used for smoke validation
- whether `prepare_summary.json` exists
- whether `predictions/manifest.json` exists
- whether `prediction_generation_status.json` exists
- whether the smoke test reached a valid success or blocking point

## Valid Outcomes

- `execution_ready`
- `execution_blocked_model_runtime`
- `execution_blocked_shell_contract`
- `execution_not_applicable`

## Must Not Do

- must not treat `phase_1 PASSED` as sufficient proof that background shell
  execution is safe
- must not skip bounded smoke validation when the final handoff surface is a
  shell entrypoint
- must not mark `execution_ready=true` if prediction generation cannot begin in
  the current environment

## Related Contracts

- [main_agent_script_routing_unit.md](/cpfs/user/jingpeng/workspace/sure-eval/docs/contracts/main_agent_script_routing_unit.md)
- [single_model_single_dataset_shell.md](/cpfs/user/jingpeng/workspace/sure-eval/docs/contracts/single_model_single_dataset_shell.md)
- [prediction_generation_contract.md](/cpfs/user/jingpeng/workspace/sure-eval/docs/contracts/prediction_generation_contract.md)

## Output Template

- [main_agent_execution_readiness_report.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_execution_readiness_report.json)
