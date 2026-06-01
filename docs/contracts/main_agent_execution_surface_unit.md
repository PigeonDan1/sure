# Main Agent EXECUTION_SURFACE_UNIT Contract

## Purpose

`EXECUTION_SURFACE_UNIT` materializes the final execution handoff artifact for
the current run.

Its job is to turn routing intent into a concrete execution surface, such as a
single-model single-dataset shell entrypoint, before execution-readiness
validation begins.

This unit exists to prevent the main flow from claiming shell readiness when
the shell itself has not yet been generated.

## Required Output

- `execution_surface_type`
- `materialized`
- `entrypoint_path`
- `generation_method`
- `resolved_inputs`
- `expected_outputs`
- `reason`
- `notes`

## Allowed Execution Surface Types

- `single_model_single_dataset_shell`
- `structured_command_bundle`
- `not_applicable`

## Required Responsibilities

A compliant execution-surface materialization should:

1. **read `script_routing.json` as primary input**: The execution surface must be derived from the steps defined in `script_routing.json`. Each step's `script`, `inputs`, and `outputs` must be reflected in the materialized surface.
2. choose the final handoff surface for the run
3. materialize the entrypoint to disk when a shell handoff is used
4. resolve model, dataset, run directory, tool name, and execution path inputs
5. record the expected output artifacts that later units should validate
6. preserve dataset task/language metadata for generation and evaluation
7. leave a stable path that `EXECUTION_READINESS_UNIT` can validate

Evaluation is dataset-driven, not only model-driven. The materialized surface
should carry enough dataset context for deterministic scripts to select the
correct language-aware post-processing, normalization, and metric behavior.

## Must Not Do

- must not claim execution readiness
- must not skip script-routing decisions
- must not validate a shell that has not been materialized
- must not emit a shell path that does not exist when `materialized=true`
- must not drop dataset language / task metadata before evaluation
- must not materialize an execution surface that deviates from the scripts and steps declared in `script_routing.json`
- must not invent new execution paths (e.g., custom Python wrappers) that bypass the scripts specified in `script_routing.json`
- must not materialize an execution surface that merely wraps or delegates to an
  external template script via `source`, `bash`, or equivalent indirection; the
  surface must be self-contained with the full execution sequence inline

## Related Contracts

- [main_agent_script_routing_unit.md](/cpfs/user/jingpeng/workspace/sure-eval/docs/contracts/main_agent_script_routing_unit.md)
- [main_agent_execution_readiness_unit.md](/cpfs/user/jingpeng/workspace/sure-eval/docs/contracts/main_agent_execution_readiness_unit.md)
- [single_model_single_dataset_shell.md](/cpfs/user/jingpeng/workspace/sure-eval/docs/contracts/single_model_single_dataset_shell.md)

## Reference Example

The current repository reference instantiation for this unit is the
`asr_qwen3` run:

- [execution_surface.json](/cpfs/user/jingpeng/workspace/sure-eval/src/sure_eval/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_001/execution_surface.json)
- [run_evaluation.sh](/cpfs/user/jingpeng/workspace/sure-eval/src/sure_eval/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_001/run_evaluation.sh)

Other onboarded, server-ready models should materialize an equivalent
execution surface, adjusted for their own task, datasets, tool name, and
server command.

## Output Template

- [main_agent_execution_surface.json](/cpfs/user/jingpeng/workspace/sure-eval/templates/main_agent_execution_surface.json)
