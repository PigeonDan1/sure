# Evaluation Run Layout Contract

## Purpose

This document defines where main-flow and evaluation artifacts should be stored
for a single model run.

The goal is to make every run auditable, reproducible, and reviewable later.

## Canonical Root

The canonical model-local root is:

```text
src/sure_eval/models/<model>/eval_runs/<run_id>/
```

## Recommended Layout

```text
src/sure_eval/models/<model>/eval_runs/<run_id>/
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
├── server_smoke_test.json
├── validation_payload.json
├── evaluation_payload.json
├── report_snapshot.md
└── predictions/
    ├── manifest.json
    ├── <dataset>.txt
    └── logs/
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

## Temporary Paths

Temporary paths may be used during execution, but they must not be the only
storage location for run evidence.

Final evidence should be copied or written into the model-local run directory
before the run is considered complete.
