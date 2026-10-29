---
name: sure-eval
description: Run reproducible SURE-EVAL audio model and tool evaluations when an agent needs readiness checks, dataset routing, prediction validation, scoring, or an auditable report.
metadata:
  short-description: Evaluate audio tools with SURE-EVAL
---

# SURE-EVAL agent skill

Use this skill when the user asks to evaluate an audio model/tool, compare tools,
or diagnose why an evaluation cannot run. Resolve this skill directory (following
symlinks); the SURE repository is two directories above it. Run commands from that
repository, using its Python environment (`python -m pip install -e .` once).
Keep run artifacts under the model's `eval_runs/<run_id>/` directory, or a
caller-specified output directory. This skill requires the SURE checkout; copying
only this folder to another machine does not install the engine.

## Discovery and loading

The checkout includes `.agents/skills/sure-eval`, a symlink to this folder, for
repository skill discovery. Start a new agent session in the checkout and invoke
`$sure-eval`. To use it from other projects, link the absolute path of this folder
into the host agent's skill directory (for Codex: `~/.agents/skills/sure-eval`).
Agents with a different loader can read this `SKILL.md` directly. See
`references/offline-smoke.md` for a reproducible offline invocation.

## Routing

1. Classify the task (ASR, KWS, TTS, VC, speech understanding, or VAD).
2. Inspect the registered model and run the readiness gate before inference:
   `python -m sure_eval.cli doctor <model> --json <run_dir>/readiness.json`.
   Exit code 0 means the static runtime checks passed; exit code 1 means blocked.
   Read the JSON even on exit 1. These checks do not load weights or prove that
   model dependencies, credentials, or inference will work.
3. If readiness is `blocked` or `repair_required`, report the failure class and
   hand off to the model-tool workflow in `docs/agents/model_tool_agent/`.
4. If ready, choose the smallest dataset scope that answers the request and use
   the deterministic scripts (`prepare_sure_dataset.py`,
   `materialize_predictions_template.py`, `validate_prediction_files.py`, and
   `evaluate_predictions.py`).
5. Validate prediction artifacts before scoring. Never treat a partial or
   schema-invalid file as a successful evaluation.

For ASR, use `python -m sure_eval.cli predict MODEL --input INPUT.jsonl --output
OUTPUT.jsonl --task asr --dry-run` before real prediction. The unified predict
adapter currently supports ASR only; route other tasks through their existing
model-tool/main-flow contracts. Unified prediction JSONL and deterministic scoring
TSV (`key<TAB>prediction`) are different formats: convert successful JSONL rows
using `instance_id` and `prediction.text`, and reject rows with `status != ok`.
Check the prediction manifest's counts and status; an exit code alone does not
prove every sample succeeded.

For existing predictions, skip model readiness and inference; validate and score
the supplied artifacts directly as shown in the offline reference. Templates
contain empty placeholders, never model predictions. Do not download data or call
a remote model unless the user has requested that evaluation.

## Reporting contract

Return a concise JSON-compatible result containing `status`, `task`, `model`,
`dataset`, `run_dir`, `readiness`, and either `metrics` or `failure_class`.
Include paths to the prediction, manifest, and report artifacts when available.
Use `blocked` for environment/tool failures and `failed` for an execution or
validation failure after readiness passed. A successful result must identify the
metric, score, sample count, and command/artifact evidence.

Read the relevant contract in `docs/agents/main_flow_agent/contracts/` only when
the task needs detailed planning, dataset selection, or report schemas.
