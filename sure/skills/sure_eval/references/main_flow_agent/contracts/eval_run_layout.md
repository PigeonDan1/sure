# Evaluation Run Layout Contract

## Purpose

This document defines where main-flow and evaluation artifacts should be stored
for a single model run.

The goal is to make every run auditable, reproducible, and reviewable later.

## Canonical Root

The canonical run root is under the selected model directory:

```text
<selected_model_dir>/eval_runs/<run_id>/
```

`selected_model_dir` is resolved in this order:

1. explicit `MODEL_DIR` / `target.model_dir`
2. `<shared-model-root>/<model>`
3. `src/sure_eval/models/<model>`

This lets collaborators publish reusable model integrations and environments
under the shared model root while keeping repo-local models as a fallback.

## Recommended Layout

```text
<selected_model_dir>/eval_runs/<run_id>/
├── task_classification.json
├── tool_readiness_routing.json
├── main_agent_plan.json
├── dataset_decision.json
├── script_routing.json
├── execution_readiness_report.json
├── assessment_report.json
├── main_agent_run_report.json
├── model_eval_manifest.json
├── prepare_summary.json
├── prediction_generation_status.json
├── evaluation_handoff.json
├── evaluation_only_status.json
├── server_smoke_test.json
├── validation_payload.json
├── evaluation_payload.json
├── protocol.yaml
├── report.jsonl
├── report_snapshot.md
└── predictions/
    ├── manifest.json
    ├── <dataset>.jsonl
    ├── <dataset>.txt
    └── logs/
└── metrics/
    └── <dataset>/
        └── <metric_slug>/
            ├── report.json
            └── pipeline_description.json
└── sample_reports/
    └── <dataset>/
        └── <metric_slug>.jsonl
```

## Minimum Required Files

Every completed or paused run directory must contain:

- `task_classification.json`
- `tool_readiness_routing.json`
- `main_agent_plan.json`
- `dataset_decision.json`
- `script_routing.json`
- `execution_readiness_report.json` when shell preflight validation has started
- `assessment_report.json`
- `main_agent_run_report.json`
- `model_eval_manifest.json`
- `prediction_generation_status.json` when prediction generation has started
- `evaluation_handoff.json` for TTS/VC runs after prediction validation passes
- `evaluation_only_status.json` for TTS/VC runs after evaluation-only retry completes

## Temporary Paths

Temporary paths may be used during execution, but they must not be the only
storage location for run evidence.

Final evidence should be copied or written into the model-local run directory
before the run is considered complete.

## Evaluation Artifact Rule

`<selected_model_dir>/eval_runs/<run_id>/` is the source of truth.

The `results/<model>/<protocol_id>/` directory may mirror `protocol.yaml`,
`report.jsonl`, `report_snapshot.md`, and compatibility prediction files, but
it must not be the only location that contains evaluation evidence.

Metric execution artifacts must be preserved per dataset and metric:

```text
metrics/<dataset>/<metric_slug>/report.json
metrics/<dataset>/<metric_slug>/pipeline_description.json
```

`report.jsonl` must use one row per dataset metric, keyed by:

```text
run_id + dataset + task + language + metric + pipeline_id
```

For TTS and VC tasks, see
[`tts_vc_audio_evaluation_surface.md`](tts_vc_audio_evaluation_surface.md).
Inference artifacts and evaluation artifacts are produced by separate execution
surfaces; the run directory is the handoff boundary between them.
