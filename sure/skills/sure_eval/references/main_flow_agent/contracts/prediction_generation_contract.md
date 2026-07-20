# Prediction Generation Contract

## Purpose

This contract defines what `wait_for_predictions` means in the main-flow
evaluation pipeline.

Its goal is to turn a vague "wait until predictions exist" step into a stable,
auditable execution boundary.

## Scope

This contract applies when the main flow has already completed:

- dataset preparation
- template materialization
- tool readiness routing

and is about to enter prediction generation through either:

- `direct_server_use`
- another approved execution path

## Required Inputs

Prediction generation should have these inputs available:

- target model name
- run id
- selected dataset list
- canonical JSONL paths
- prediction template manifest
- execution path
- tool name or server entrypoint

For TTS and VC datasets only, prediction generation should also preserve and
forward available prompt/reference text fields from the canonical sample into
the model tool call. Accepted source fields include `prompt_text`, `ref_text`,
`reference_text`, and task-defined reference text fields. Some TTS models use
ASR fallback when this text is absent; in that case the failure should be
classified as a TTS inference-input or runtime-surface issue before
classifying the model itself as broken.

## Required Outputs

Prediction generation must produce or update:

- `predictions/<dataset>.txt`
- `predictions/<dataset>.jsonl`
- `predictions/manifest.json`
- `predictions/logs/<dataset>.log` when logs are captured
- `prediction_generation_status.json`

## Completion Criteria

`wait_for_predictions` can be marked complete only when all selected datasets
meet the following conditions:

1. the expected prediction file exists
1. the structured prediction JSONL exists when prediction generation runs
   through `scripts/generate_predictions_via_server.py`
2. the prediction file path matches the run manifest
3. generation status is recorded for the dataset
4. the dataset is ready to enter `validate_prediction_files.py`

This step does **not** require the predictions to be correct, but it does
require them to be fully generated for the selected scope.

## Allowed Status Values

Per-dataset generation status should use:

- `pending`
- `running`
- `completed`
- `failed`
- `skipped`

Run-level prediction generation status should use:

- `waiting_for_prediction_generation`
- `prediction_generation_in_progress`
- `prediction_generation_completed`
- `prediction_generation_failed`

## Required Fields for `prediction_generation_status.json`

- `run_id`
- `model_name`
- `execution_path`
- `tool_name`
- `datasets`

Each dataset item should include:

- `dataset`
- `prediction_file`
- `structured_prediction_file`
- `status`
- `num_expected_samples`
- `num_generated_samples`
- `log_path`
- `error`

## Resume Rule

Prediction generation may be resumed.

If resumed:

- existing prediction files must not be silently discarded
- resumed progress should update `prediction_generation_status.json`
- partial progress should remain reviewable

## Output Contract

Structured prediction rows must follow:

- [prediction_output_contract.md](prediction_output_contract.md)

The TSV file is a compatibility projection of the structured JSONL row. Its
second column must equal the row's `normalized_prediction`.

## Must Not Do

- must not treat an empty template as completed generation
- must not skip generation status recording
- must not proceed directly to evaluation before validation
- for TTS/VC, must not drop available prompt/reference text before calling the
  model tool

## Related Files

- [main_agent_script_routing_unit.md](main_agent_script_routing_unit.md)
- [eval_run_layout.md](eval_run_layout.md)
