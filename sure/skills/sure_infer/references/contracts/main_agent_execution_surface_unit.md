# Main Agent EXECUTION_SURFACE_UNIT Contract

## Purpose

`EXECUTION_SURFACE_UNIT` records the execution handoff artifact for the
current run.

The artifact, `execution_surface.json`, is written by `scripts/run_infer.py`
from the bundled `scripts/infer_entrypoint.py`. The agent runs
`scripts/run_infer.py --run-dir <sure_run_dir>` once; the script resolves the
approved binding, datasets, run directory, tool name and execution path, writes
the surface, runs the compliance checks and launches inference in the approved
runtime. The agent never authors the JSON by hand.

This unit exists to prevent the main flow from claiming execution readiness
when no surface has been written.

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

- `python_entrypoint`
- `not_applicable`

## Required Responsibilities

A compliant execution-surface unit should:

1. **read `script_routing.json` as primary input**: the steps declared there are the bundled scripts `infer_entrypoint.py` runs; the surface must not add to or bypass them.
2. run `scripts/run_infer.py --run-dir <sure_run_dir>` once
3. leave `execution_surface.json` and `execution_result.json` under `artifacts/` for `EXECUTION_READINESS_UNIT`, `SMOKE_TEST_UNIT` and `EXECUTE_WAIT_UNIT` to validate
4. preserve dataset task/language metadata (`resolved_inputs.datasets`, `dataset_entries`) for generation and evaluation

Evaluation is dataset-driven, not only model-driven. The surface carries enough
dataset context for deterministic scripts to select the correct language-aware
post-processing, normalization, and metric behavior.

## Must Not Do

- must not claim execution readiness
- must not skip script-routing decisions
- must not write or edit `execution_surface.json` by hand
- must not run any entrypoint other than the bundled `scripts/infer_entrypoint.py`, or wrap it in a custom script
- must not reference prior `eval_runs` or copy prior-run scripts
- must not drop dataset language / task metadata before evaluation

## Related Contracts

- [main_agent_script_routing_unit.md](main_agent_script_routing_unit.md)
- [main_agent_execution_readiness_unit.md](main_agent_execution_readiness_unit.md)

## Output

`execution_surface.json` is written by `scripts/run_infer.py` from
[`scripts/infer_entrypoint.py`](../../scripts/infer_entrypoint.py); there is no
template to fill in. `scripts/check_execution_surface_compliance.py` verifies
that the recorded entrypoint path and sha256 are the bundled file's.
