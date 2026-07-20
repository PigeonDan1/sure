# Single Model Single Dataset Shell Contract

## Purpose

This contract defines the shell entrypoint for one complete evaluation run on:

- one model
- one dataset

The goal is to provide a command that a human can launch in the background
without manually reconstructing the main-flow execution order.

## Required Responsibilities

A compliant shell should:

1. create a model-local run directory
2. write or reserve the run artifact locations
3. run dataset preparation
4. materialize prediction templates
5. run prediction generation through the approved execution path
6. run prediction validation
7. run evaluation
8. update or write final evidence paths
9. support a bounded smoke-test mode for execution-readiness validation

## Required Inputs

A shell entrypoint must clearly define:

- target model
- target dataset
- dataset task and language, when known
- run id
- output run directory
- execution path
- tool name when using direct server execution
- a bounded smoke-test control surface such as `MAX_SAMPLES`

## Required Outputs

A compliant shell must leave evidence under:

```text
<selected_model_dir>/eval_runs/<run_id>/
```

and produce at least:

- `prepare_summary.json`
- `predictions/manifest.json`
- `prediction_generation_status.json`
- `validation_payload.json`
- `evaluation_payload.json`

`evaluation_payload.json` must preserve the dataset-driven evaluation context,
including the dataset language, selected metric, route, node list, and
post-processing / normalization / conversion policy used by the deterministic
evaluator.
If multiple metrics are requested, the shell must pass repeated
`--metric <metric>` arguments to `evaluate_predictions.py`; otherwise it should
leave metric selection to the dataset SOTA baseline or task default.

The evaluation step must call the deterministic evaluator entrypoint rather
than reimplementing scoring in the generated shell. For every supported task,
metric computation must enter `sure_eval.evaluation.scripts.run_task(...)` and
select its route from `src/sure_eval/evaluation/tasks/<task>/routes.yaml`.
ASR routes are still router-backed routes: they commonly use in-process
normalization nodes followed by WER/CER/MER scoring nodes. TTS/VC routes use
the same router but may select node-local transcription, speaker-similarity,
and MOS providers.
For supported tasks, that entrypoint is expected to follow the compatible
evaluation-pipeline behavior for ASR, code-switch ASR, SER, GR, S2TT, SLU, SD,
SA-ASR, TTS, and VC.
SD and SA-ASR are special because `evaluate_predictions.py` must materialize
MeetEval-loadable annotation files before calling `sure_eval.evaluation.scripts.run_task(...)`.
When the selected metric route reports conversion profiles, the shell and
summary payloads must preserve `conversion_steps` / `conversion_trace` so the
model-output-to-metric-input adapter remains auditable.

When used for preflight validation, the shell should also make it possible to
verify:

- shell syntax
- bounded prediction-generation startup
- `prediction_generation_status.json` update behavior

## Required Ordering

The shell must respect this order:

1. `prepare_sure_dataset.py`
2. `materialize_predictions_template.py`
3. `generate_predictions_via_server.py` or another approved generation step
4. `validate_prediction_files.py`
5. `evaluate_predictions.py`

## Must Not Do

- must not skip validation before evaluation
- must not write final evidence only to `/tmp`
- must not silently replace a previous run directory
- must not require a full dataset run just to validate whether the shell works
- must not treat evaluation as language-agnostic when the dataset declares a
  language
- must not compute WER/CER/MER, classification scores, diarization metrics, or
  audio metrics directly in the shell or model image instead of the route-backed
  evaluator
- must not delegate the 5-step execution sequence to an external template script
  via `source`, `bash`, or equivalent indirection; the materialized shell must be
  self-contained and contain the full execution sequence inline

## Recommended Template

- [run_single_model.sh](docs/agents/main_flow_agent/templates/run_single_model.sh)
