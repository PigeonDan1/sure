# Model Evaluation Manifest Contract

## Purpose

`model_eval_manifest.json` is the single-file index for one model evaluation
run.

It should be the first file a reviewer opens when inspecting a run under the
selected model directory:

```text
<selected_model_dir>/eval_runs/<run_id>/
```

The selected model directory may come from an explicit `MODEL_DIR`, the shared
model root `/hpc_stor03/sjtu_home/jing.peng/workspace/model/<model>`, or the
repo-local fallback `src/sure_eval/models/<model>`.

Its role is to connect:

- main-flow decision artifacts
- deterministic script outputs
- prediction files
- dataset evidence
- final evaluation evidence
- run-local protocol, dataset-metric report, metric pipeline artifacts, and
  sample reports

## Required Output

- `run_id`
- `model_name`
- `model_dir`
- `created_at`
- `status`
- `selected_datasets`
- `artifacts`
- `prediction_files`
- `dataset_jsonl_paths`
- `report_jsonl`
- `protocol`
- `metric_artifacts`
- `sample_reports`

## Allowed Status Values

- `in_progress`
- `waiting_for_prediction_generation`
- `blocked`
- `partial_success`
- `success`

## Required Artifact Keys

The `artifacts` object should track these keys:

- `task_classification`
- `tool_readiness_routing`
- `main_agent_plan`
- `dataset_decision`
- `script_routing`
- `execution_readiness_report`
- `assessment_report`
- `main_agent_run_report`
- `prepare_summary`
- `server_smoke_test`
- `prediction_manifest`
- `validation_payload`
- `evaluation_payload`
- `protocol`
- `report_jsonl`
- `report_snapshot`

## Aggregation Rule

The manifest is an index, not a replacement for upstream files.

It must point to the artifact paths produced during the run.

## Must Not Do

- must not hide missing artifacts
- must not claim `success` if validation/evaluation artifacts are absent
- must not replace `main_agent_run_report.json`

## Output Template

- [model_eval_manifest.json](../templates/model_eval_manifest.json)
