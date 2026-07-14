# /sure_eval

Orchestrate a SURE-EVAL evaluation run for an already-onboarded audio model. This skill is the Sure port of the SURE-EVAL main-flow agent. The state machine lives in `hooks/state-machine.ts`; this document is what the agent reads to drive each unit.

Control principle: **agent decides scope, scripts enforce format and execution.** You (the agent) choose which datasets, what target, how to route; the deterministic scripts under `scripts/` and the hook gates enforce that every artifact is in the right place, the right format, and the right value domain — and that the two red lines hold.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `model` | ✅ | Onboarded model name. Resolves to `sure/models/<model>/`; the hook reads `verdict.json`/`config.yaml`/`server.py` there to judge readiness. |
| `task` | ✅ | `asr \| tts \| vc \| kws \| s2tt \| speech_understanding`. |
| `datasets` | — | Dataset list; if omitted, `dataset_scope` auto-selects. |
| `target` | — | Target metric or paper to compare against. |
| `max_samples` | — | Sample cap (smoke / fast test). |
| `execution_path` | — | `vc_submit \| local_bash \| local_docker`. vc_submit is **mandatory** when vc is available. |
| `audit` | — | When true, triage existing results instead of running a new evaluation. |
| `model_dir` | — | Override the default `sure/models/<model>/` (cross-skill handoff). |
| `run_id` | — | Resume a specific run. |
| `template` | — | Execution-surface template name (under `scripts/templates/`). |

The run directory (`<run_dir>`, provided by the Sure invocation) holds structured outputs under `<run_dir>/artifacts/<unit.produces>`.

## State Machine

Advance happens **only** when the current unit's `produces` artifact is compliant (location + format + value domain; no forbidden fields). Linear units are agent self-driven; gate units additionally run a Python semantic check. Produce the current unit's artifact, then call `sure_update_state`.

| # | Unit | Kind | Produces | Gate script |
|---|------|------|----------|-------------|
| 1 | `task_classification` | linear | `task_classification.json` | — |
| 2 | `tool_readiness_routing` | **gate** | `tool_readiness_routing.json` | — (in-process: blocks on `handoff_to_tool_agent=true`) |
| 3 | `plan` | linear | `main_agent_plan.json` | — |
| 4 | `dataset_scope` | linear | `dataset_decision.json` | — |
| 5 | `script_routing` | **gate** | `script_routing.json` | `scripts/check_script_routing.py` |
| 6 | `execution_surface` | **gate** | `execution_surface.json` (+ `run_evaluation.sh`) | — |
| 7 | `execution_readiness` | **gate** | `execution_readiness_report.json` | `scripts/check_execution_surface_compliance.py` |
| 8 | `smoke_test` | **gate** | `smoke_test_result.json` | `scripts/run_smoke.py` |
| 9 | `submit_vc_run` | **gate** | `submit_result.json` | `scripts/vc_check.py` |
| 10 | `execute_wait` | linear | `execution_result.json` | — |
| 11 | `assessment` | **gate** | `assessment_report.json` | `scripts/check_assessment.py` |
| 12 | `run_report` | **gate** | `main_agent_run_report.json` | `scripts/check_run_report.py` |

### Per-unit contract (Inputs → Output → Allowed → Must Not Do → Failure)

Each unit must satisfy: **Inputs** (previous unit's produces + evidence sources to read) → **Output** (`produces` JSON, schema in `schemas/`) → **Allowed** (value domain) → **Must Not Do** (forbidden fields that belong to later units — anti step-merge) → **Failure** classification.

- **task_classification**: Inputs = `model` param + `sure/models/<model>/model.spec.yaml`. Output = `task_classification.json` {task_type, rationale}. Allowed: task_type ∈ {asr,tts,vc,kws,s2tt,speech_understanding}. Must Not Do: do not set `execution_path`/`report_persisted` (later units).
- **tool_readiness_routing**: Inputs = `sure/models/<model>/verdict.json`, `config.yaml`, `server.py`. Output = {readiness, model_dir, ...}. Allowed: readiness ∈ {ready, needs_onboarding, needs_repair, unavailable}. If `handoff_to_tool_agent=true` the run blocks — run `/sure_onboard` first.
- **plan** / **dataset_scope** / **execution_surface** / **execute_wait**: produce the declared JSON; see `schemas/`. Do not emit later-unit fields.
- **script_routing**: Output steps[] each {name, script}. name ∈ the whitelist (see `schemas/script_routing.schema.json`); `script` must resolve under `scripts/`.
- **execution_surface**: Output {entrypoint, source_provenance.template_file}. The `run_evaluation.sh` MUST be derived ONLY from a template under `scripts/templates/`. Must Not Do: `eval_runs_referenced`, `prior_run_scripts_copied` (cross-run leakage).
- **execution_readiness**: red line 1. {execution_ready, smoke_test_passed, isolation_audit.audit_passed} all true. `check_execution_surface_compliance.py` confirms the template path is under `scripts/templates/`.
- **smoke_test**: bounded smoke on a tiny slice; `smoke_passed` true.
- **submit_vc_run**: red line 2. {execution_path, vc_available}. When vc is available, `execution_path` must be `vc_submit`.
- **assessment**: {anomaly_detected, user_confirmed}. Anomaly (e.g. WER/CER > 50%, Accuracy < 20%) requires user confirmation.
- **run_report**: {report_persisted, execution_path_actual}. Non-vc_submit paths require `fallback_approved` + `local_fallback_reason`.

## System Constraints (red lines — non-negotiable)

```
[SYSTEM_CONSTRAINT: EXECUTION_SURFACE_ISOLATION]
When materializing the execution surface (run_evaluation.sh):
1. ALLOWED_TEMPLATES_ROOT: "scripts/templates/"
   - The generated script MUST be derived ONLY from a template under ALLOWED_TEMPLATES_ROOT.
   - You MUST NOT use any template outside ALLOWED_TEMPLATES_ROOT.
2. TEMPLATE_DECLARATION:
   - execution_surface.json -> source_provenance.template_file MUST contain the
     exact path of the template used, and it MUST resolve under scripts/templates/.
3. SELF_VERIFICATION:
   - Before declaring execution_ready=false if unsure. The execution_readiness gate
     runs scripts/check_execution_surface_compliance.py against the declared template.

[SYSTEM_CONSTRAINT: VC_SUBMIT_MANDATORY]
When the Volcano (vc) CLI is installed and available:
1. VC_AVAILABILITY_CHECK:
   - Run `which vc` AND `vc info` before ANY local execution. (vc is installed at /usr/bin/vc.)
   - If both succeed, `vc submit` is the ONLY permitted execution path, regardless of deployment type or Docker availability.
   - `vc --version` MUST NOT be used to determine availability.
2. LOCAL_EXECUTION_PROHIBITED:
   - Local `bash run_evaluation.sh` or `docker run` is STRICTLY PROHIBITED when vc is available.
3. FALLBACK_CONDITIONS:
   - Local execution is ONLY permitted when `which vc` fails OR `vc info` definitively fails,
     AND fallback_approved=true + a non-empty local_fallback_reason is recorded.
```

## Backend

The deterministic backend is bundled in `scripts/` (self-contained — no external repo reference). The package `scripts/sure_eval/` holds the framework (agent/evaluator.py, evaluation/, inference/, datasets/, reports/, protocols/, models/registry.py). Flat scripts under `scripts/` (`prepare_sure_dataset.py`, `materialize_predictions_template.py`, `generate_predictions_via_server.py`, `validate_prediction_files.py`, `evaluate_predictions.py`, `refresh_report_snapshot.py`, `check_execution_surface_compliance.py`) are the routing targets. Templates live in `scripts/templates/`. Run them as:

```bash
python3 scripts/<script>.py <args>   # cwd = skill package dir
```

## Gate Checks (enforced by hooks)

- `script_routing`: steps whitelisted, scripts under `scripts/`.
- `execution_readiness`: `execution_ready && smoke_test_passed && isolation_audit.audit_passed`; `check_execution_surface_compliance.py` (red line 1).
- `smoke_test`: `smoke_passed` true; entrypoint exists.
- `submit_vc_run`: `vc_check.py` (red line 2: vc mandatory when available).
- `assessment`: anomaly → `user_confirmed` true.
- `run_report`: `report_persisted` true, `execution_path_actual` declared; non-vc_submit needs approval + reason.

On gate failure the hook blocks with a `repair` message and bumps the retry counter (max 3); beyond that the unit is marked FAILED — classify via `references/failure_taxonomy.md` and repair or finish with `status: failed`. Do not blind-retry.

## Success Criteria

The `pre_finish` hook enforces: `main_agent_run_report.json` exists, the terminal gate passes, and the state machine reached the terminal unit. On success call `sure_finish` with `status: "success"` and `manifest_path: ".sure/runs/<run_id>/manifest.json"`. If incomplete or blocked, finish with `status: "incomplete"` or `status: "failed"` and a repair summary.
