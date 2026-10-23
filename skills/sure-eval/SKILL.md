---
name: sure-eval
description: Run reproducible SURE-EVAL audio model and tool evaluations when an agent needs readiness checks, dataset routing, prediction validation, scoring, or an auditable report.
metadata:
  short-description: Evaluate audio tools with SURE-EVAL
---

# SURE-EVAL agent skill

Use this skill when the user asks to evaluate an audio model/tool, compare tools,
or diagnose why an evaluation cannot run. Work from the repository root and keep
all run artifacts under the model's `eval_runs/<run_id>/` directory.

## Routing

1. Classify the task (ASR, KWS, TTS, VC, speech understanding, or VAD).
2. Inspect the registered model and run the readiness gate before inference:
   `python -m sure_eval.cli doctor <model> --json <run_dir>/readiness.json`.
3. If readiness is `blocked` or `repair_required`, report the failure class and
   hand off to the model-tool workflow in `docs/agents/model_tool_agent/`.
4. If ready, choose the smallest dataset scope that answers the request and use
   the deterministic scripts (`prepare_sure_dataset.py`,
   `materialize_predictions_template.py`, `validate_prediction_files.py`, and
   `evaluate_predictions.py`).
5. Validate prediction artifacts before scoring. Never treat a partial or
   schema-invalid file as a successful evaluation.

For a smoke test, use `sure-eval predict ... --dry-run` first, then run the real
prediction command only after the dry run passes. Do not download data or call a
remote model unless the user has requested that evaluation.

## Reporting contract

Return a concise JSON-compatible result containing `status`, `task`, `model`,
`dataset`, `run_dir`, `readiness`, and either `metrics` or `failure_class`.
Include paths to the prediction, manifest, and report artifacts when available.
Use `blocked` for environment/tool failures and `failed` for an execution or
validation failure after readiness passed. A successful result must identify the
metric, score, sample count, and command/artifact evidence.

Read the relevant contract in `docs/agents/main_flow_agent/contracts/` only when
the task needs detailed planning, dataset selection, or report schemas.
