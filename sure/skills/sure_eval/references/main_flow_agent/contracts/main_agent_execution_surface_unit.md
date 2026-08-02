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

Model directory resolution must be deterministic:

1. an explicit `MODEL_DIR` / `target.model_dir` wins
2. otherwise check `<shared-model-root>/<model>`
3. otherwise fall back to `src/sure_eval/models/<model>`

The selected `model_dir`, `model_dir_source`, `shared_model_root`, and
`repo_model_root` must be recorded in `resolved_inputs`. The run directory
should be created under the selected `model_dir` unless the user explicitly
overrides `RUN_DIR`.

For TTS inference, selecting the shared model root is a valid way to reuse
shared checkpoints and runtime assets. If the user requires evidence or
generated artifacts to live under the repo-local model directory, the execution
surface should keep the shared checkpoint/model source valid and explicitly set
`run_dir` / `RUN_DIR` under `src/sure_eval/models/<model>/eval_runs/<run_id>`.
Do not reclassify the shared checkpoint root as a failure.

For TTS inference under `vc submit`, runtime interpreter resolution must be
auditable. If the image does not expose `/opt/<model>_venv/bin/python`, record
the explicit interpreter path in
`resolved_inputs.vc_runtime_contract.runtime_paths.container_python_path`.
This field may be `python` when the container image makes the intended
interpreter available on `PATH`.

Evaluation is dataset-driven, not only model-driven. The materialized surface
should carry enough dataset context for deterministic scripts to select the
correct route-backed post-processing, normalization, conversion, transcription,
and metric behavior under `src/sure_eval/evaluation/tasks/<task>/routes.yaml`.
When multiple metrics are required, `resolved_inputs.evaluation_metrics` should
list them in execution order. An empty list means the evaluator will use the
dataset SOTA baseline metric when available and otherwise the task default.

For all tasks, the evaluation surface must call `scripts/evaluate_predictions.py`
or the configured `sure-eval metric describe/run` path, and metric computation
must enter `sure_eval.evaluation.scripts.run_task(...)`. ASR routes may be
lightweight in-process normalization plus WER/CER/MER scoring, while TTS/VC
routes may require node-local transcription, speaker, and MOS providers. This
runtime difference does not change the router requirement.

For TTS and VC tasks, the execution surface must make the artifact handoff
between inference and evaluation explicit:

- inference surface: generate and validate `predictions/<dataset>.txt`,
  `predictions/<dataset>.jsonl`, prediction logs, and `validation_payload.json`
- evaluation surface: run the same repository evaluation router, with
  node-local uv providers selected by the TTS/VC audio routes; if cluster GPU
  resources are needed, `vc submit` is only the scheduler/container shell, not
  the metric dependency surface
- retry surface: if only evaluation failed after validation passed, launch an
  evaluation-only surface that consumes the existing prediction artifacts

The node-local dependency preflight evidence, and any base runtime/interpreter
requirements when running through `vc submit`, should be recorded in
`resolved_inputs` or `notes`. For semantic TTS/VC metrics, the preflight must
include `ffmpeg` and the configured transcription runtime cache/provider.

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
- must not implement metric scoring in the generated shell or model image
  instead of routing through `src/sure_eval/evaluation`
- for TTS/VC, must not hide audio metric execution inside the model inference
  image or route metrics through per-metric dependency images when the
  node-local uv workflow under `src/sure_eval/evaluation` is available
- for TTS/VC, must not regenerate prediction artifacts when materializing an
  evaluation-only retry surface after prediction validation has already passed
- for TTS, must not prepend host `.venv`, `.venv.hostbak`, or host
  site-packages directories to container `PYTHONPATH` during model inference
- for TTS, must not infer a nonexistent container venv path from the model
  name when the execution surface has or needs an explicit
  `runtime_paths.container_python_path`

## Related Contracts

- [main_agent_script_routing_unit.md](main_agent_script_routing_unit.md)
- [main_agent_execution_readiness_unit.md](main_agent_execution_readiness_unit.md)
- [single_model_single_dataset_shell.md](single_model_single_dataset_shell.md)

## Reference Example

The current repository reference instantiation for this unit is the
`asr_qwen3` run:

- [execution_surface.json](../../../src/sure_eval/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_001/execution_surface.json)
- [run_evaluation.sh](../../../src/sure_eval/models/asr_qwen3/eval_runs/main_agent_asr_qwen3_001/run_evaluation.sh)

Other onboarded, server-ready models should materialize an equivalent
execution surface, adjusted for their own task, datasets, tool name, and
server command.

## Output Template

- [main_agent_execution_surface.json](../templates/main_agent_execution_surface.json)
