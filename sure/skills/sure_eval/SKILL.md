# /sure_eval

Orchestrate a SURE-EVAL evaluation run for an already-onboarded audio model. This skill is the Sure port of the SURE-EVAL main-flow agent. The state machine lives in `hooks/state-machine.ts`; this document is what the agent reads to drive each unit.

The upstream main-flow reference is mirrored read-only under `references/main_flow_agent/` for audit and parity review only. It is not a runtime template or execution source. Do not edit `/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/docs/agents/main_flow_agent`; refresh the mirror with `scripts/check_main_flow_reference.py --sync` and adapt harness execution only under this skill package.

**Prerequisite**: run `/sure_init` first to select an agent, configure auth, and validate the environment for this project.

Control principle: **agent decides scope, scripts enforce format and execution.** You (the agent) choose which datasets, what target, how to route; the deterministic scripts under `scripts/` and the hook gates enforce that every artifact is in the right place, the right format, and the right value domain — and that execution-surface isolation plus the user-selected execution policy hold.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `model` | ✅ | Onboarded model name. Resolves to `sure/models/<model>/`; the hook reads `verdict.json`/`config.yaml`/`server.py` there to judge readiness. |
| `datasets` | ✅ | Comma-separated dataset list in the `/sure_eval` command, e.g. `datasets=aishell1,seedtts_test_eval_zh`. Dataset metadata, not a user-supplied task flag, determines ASR/TTS/VC/etc. |
| `device` | — | `auto \| cpu \| cuda \| cuda:<index>`. Default `auto`; resolved by `scripts/resolve_eval_input.py` and passed through inference/evaluation templates when materialized. For `execution=local`, `cuda:<index>` selects the local host GPU by setting `CUDA_VISIBLE_DEVICES=<index>`. For `execution=vc`, the allocated container GPU is addressed as `cuda:0`; choose hardware with `vc_partition`/`vc_gpu`, not a host CUDA ordinal. |
| `target` | — | Target metric or paper to compare against. |
| `max_samples` | — | Sample cap for bounded validation runs. Omitted or `0` means full dataset. |
| `execution` | — | `auto \| local \| vc`. Default `auto`; `local` is an explicit user choice, `vc` requires a real vc submission, `auto` prefers vc when available. |
| `execution_path` | — | Legacy alias: `auto \| vc_submit \| local_bash \| local_docker`. Normalize this into `execution` in `eval_input_resolved.json`. |
| `vc_partition` / `vc_cpu` / `vc_mem` / `vc_gpu` / `vc_image` | — | Optional vc resource overrides recorded in the execution surface and submit result. |
| `audit` | — | When true, triage existing results instead of running a new evaluation. |
| `model_dir` | — | Override model directory resolution. If omitted, resolution checks `SURE_MODELS_DIR`, `SURE_MODEL_ROOT`, `LEGACY_SURE_MODELS_DIR`, `LEGACY_SURE_EVAL_ROOT`, then `sure/models/<model>/`. |
| `run_id` | — | Resume a specific run. |
| `template` | — | Execution-surface template name under `scripts/templates/`. |

The run directory (`<run_dir>`, provided by the Sure invocation) holds structured outputs under `<run_dir>/artifacts/<unit.produces>`. The model-local evaluation run directory recorded in `eval_input_resolved.json -> runtime.run_dir` remains the source of truth for prediction, protocol, report, metric, and sample-level artifacts.

At `pre_start`, the hook resolves the product input into
`<run_dir>/artifacts/eval_input_resolved.json` by running
`scripts/resolve_eval_input.py`. This artifact is the bridge from the
user-friendly `/sure_eval model=... datasets=... device=...` surface to the
upstream `MAIN_FLOW_INPUT` shape documented in
`docs/agents/main_flow_agent/README.md`:

- `target.model_name/model_dir/tool_workflow_ready/integration_state` comes from
  the onboarded model directory.
- `constraints.allowed_datasets` is the canonical expanded dataset list.
- `constraints.allowed_tasks` is inferred from dataset JSONL metadata.
- `runtime_context.output_dir`, `device_request`, `device_resolved`, execution
  request/plan, `max_samples` sample scope, and
  `available_scripts` are recorded before the state machine starts.

Do not ask the user for `task` as the primary input. If a legacy prompt includes
`task=asr` or similar, treat it only as a consistency hint; the source of truth
for evaluation task routing is the dataset metadata resolved into
`eval_input_resolved.json`.

At `execution_readiness`, the hook also resolves
`<run_dir>/artifacts/evaluation_route_plan.json` by calling the standalone
`sure-evaluation` route/capability contract through
`scripts/resolve_evaluation_route_plan.py`. This artifact is the source of
truth for supported metrics, default metrics, selected metric route choices,
pipeline ids, node chains, required input roles, and selected node environment
readiness. Do not infer metric support, normalization, transcription, or
scoring chains inside harness code when this plan is available.

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

- **task_classification**: Inputs = `eval_input_resolved.json` + `model` param + `sure/models/<model>/model.spec.yaml`. Output = `task_classification.json` {task_type, reason, need_tool_workflow, confidence, input_signals}. Allowed: task_type ∈ {onboarding_then_evaluate,evaluate_existing_model,repair_broken_model,audit_results}. Must Not Do: do not select datasets or set `execution_path`/`report_persisted` (later units).
- **tool_readiness_routing**: Inputs = resolved model dir (`model_dir`, env model root, or `sure/models/<model>/`) with `verdict.json` or `artifacts/verdict.json`, `config.yaml`, `server.py`. Output = {readiness, model_dir, ...}. Allowed: readiness ∈ {ready, needs_onboarding, needs_repair, unavailable}. If `handoff_to_tool_agent=true` the run blocks — run `/sure_onboard` first.
- **plan**: Inputs = task classification + tool readiness + `eval_input_resolved.json`. Output follows `main_agent_plan.schema.json` and describes execution order only.
- **dataset_scope**: Inputs = `eval_input_resolved.json` + explicit human constraints. Output = {selection_basis, selected_datasets, skipped_datasets}. User-provided datasets are validated/canonicalized here; this unit should not silently invent a different dataset scope.
- **execution_surface** / **execute_wait**: produce the declared JSON; see `schemas/`. Do not emit later-unit fields.
- **script_routing**: Output steps[] each {name, script}. name ∈ the whitelist (see `schemas/script_routing.schema.json`); `script` must resolve under `scripts/`.
- **execution_surface**: Output {entrypoint_path or entrypoint, source_provenance.template_file}. The `run_evaluation.sh` MUST be derived ONLY from a template under `scripts/templates/`. The reference mirror is audit-only. Local execution surfaces must declare `env.MODEL_PYTHON` as an absolute path to the model interpreter; `TOOL_NAME`, when set, must match the tool declared in the model's `config.yaml`. If the model requires framework imports before execution, declare them in `inference_runtime.required_imports` or the model `config.yaml`; `inference_runtime.import_probe_policy` defaults to `declared_only`, with `skip` and `required` available for explicit surfaces. Harness does not assume every model is PyTorch. Must Not Do: `eval_runs_referenced`, `prior_run_scripts_copied` (cross-run leakage).
- **execution_readiness**: red line 1. `execution_ready=true` and `isolation_audit.audit_passed=true`; bounded smoke is checked by the following `smoke_test` unit, not here. `check_execution_surface_compliance.py` confirms the template path is under an approved template root, verifies declared hashes when present, rejects prior-run leakage, requires `evaluate_predictions.py` calls to declare the main-flow evaluation args including `--evaluation-backend`, verifies the local inference runtime (declared interpreters exist and are executable, explicitly declared `required_imports` import successfully, the declared tool name matches the model's `config.yaml`), and writes/validates `evaluation_route_plan.json` through the standalone `sure-evaluation` engine. If any selected route has blocking node environment issues, execution readiness fails and the repair message must point to the setup command from the route plan.
- **smoke_test**: bounded smoke on a tiny slice; `smoke_passed` true.
- **submit_vc_run**: execution policy gate. {execution_path, vc_available}. `execution=vc` must use `vc_submit`; `execution=local` may use `local_bash`/`local_docker` even when vc is available; `execution=auto` prefers `vc_submit` when vc is available and must record a fallback reason if it runs locally. Before any real submission `scripts/run_vc_execution.py` runs a parameter precheck (image existence via local docker or the vc probe, partition membership, container path visibility, venv layout, resource shape), writes `vc_precheck.json`, and refuses to submit on failure; run `scripts/check_vc_submit_readiness.py` standalone to see failures and real candidates.
- **assessment**: {anomaly_detected, user_confirmed}. Anomaly (e.g. WER/CER > 50%, Accuracy < 20%) requires user confirmation.
- **run_report**: {report_persisted, execution_path_actual}. Record `execution_path_requested`, `execution_path_actual`, `device_request`, `device_actual`, `max_samples`, total dataset samples, and evaluated samples. Non-vc paths are valid for explicit `execution=local`; auto local fallback requires a reason and, if vc was available, explicit fallback approval.

## System Constraints (red lines — non-negotiable)

```
[SYSTEM_CONSTRAINT: EXECUTION_SURFACE_ISOLATION]
When materializing the execution surface (run_evaluation.sh):
1. ALLOWED_TEMPLATE_ROOTS: "scripts/templates/"
   - The generated script MUST be derived ONLY from a template under this approved root.
   - Use `scripts/templates/` for harness-adapted executable templates.
   - `references/main_flow_agent/templates/` is audit-only and MUST NOT be used as a runtime template root.
   - You MUST NOT use any template outside this root.
2. TEMPLATE_DECLARATION:
   - execution_surface.json -> source_provenance.template_file MUST contain the
     exact path of the template used, and it MUST resolve under an approved root.
3. SELF_VERIFICATION:
   - Before declaring execution_ready=false if unsure. The execution_readiness gate
     runs scripts/check_execution_surface_compliance.py against the declared template.

[SYSTEM_CONSTRAINT: EXECUTION_POLICY]
The user controls where formal model inference runs:
1. EXECUTION_REQUEST:
   - `execution=local`: run the materialized surface locally. This is valid even when vc is available.
   - `execution=vc`: submit through vc. If `which vc && vc info` fails, the run must fail instead of falling back.
   - `execution=auto` or omitted: prefer vc when available; otherwise local fallback is allowed and must record the reason.
2. DEVICE_REQUEST:
   - `device=cpu` hides `CUDA_VISIBLE_DEVICES`.
   - `device=cuda:<index>` records the user request, sets `CUDA_VISIBLE_DEVICES=<index>` for local execution, and records process-visible `device_actual=cuda:0`.
   - For `execution=vc`, `device=auto|cuda|cuda:<index>` resolves to the container-visible `cuda:0`; a nonzero requested ordinal is preserved in provenance with an explanatory note because VC physical GPU selection is controlled by `vc_partition`/`vc_gpu`.
3. PROVENANCE:
   - `submit_result.json`, `execution_result.json`, `prediction_generation_status.json`, and `main_agent_run_report.json` must make the formal execution location auditable. Do not confuse `inference_call_mode=direct_server_use` with `execution_path=local_bash|vc_submit`.
```

## Backend

The deterministic harness backend is bundled in `scripts/`. The package
`scripts/sure_eval/` holds the model, dataset, inference, report, and legacy
evaluation compatibility code. Formal metric execution should prefer the
standalone `sure-evaluation` engine when it is available. Flat scripts under
`scripts/` (`resolve_eval_input.py`, `resolve_model_dir.py`,
`resolve_evaluation_engine.py`,
`resolve_evaluation_route_plan.py`,
`run_model_mcp_smoke.py`, `prepare_sure_dataset.py`,
`materialize_predictions_template.py`, `generate_predictions_via_server.py`,
`validate_prediction_files.py`, `evaluate_predictions.py`,
`refresh_report_snapshot.py`, `run_local_execution.py`, `run_vc_execution.py`,
`check_execution_surface_compliance.py`) are the routing targets. Templates live
in `scripts/templates/`. Run them as:

```bash
python3 scripts/<script>.py <args>   # cwd = skill package dir
```

For `execution=local`, call `scripts/run_local_execution.py --run-dir <sure_run_dir>`
from the submit unit. It runs the materialized `run_evaluation.sh` and writes
both `submit_result.json` and `execution_result.json`. For `execution=vc`, use
`scripts/run_vc_execution.py --run-dir <sure_run_dir>` from the submit unit. It
writes `submit_result.json`, includes the exact `vc submit` command and a
persistent `<sure_run_dir>/vc_logs/job.log`, and leaves final
`execution_result.json` to the following `execute_wait` unit. When vc resources
are selected or overridden at submit time, the effective image, partition, CPU,
GPU, memory, entrypoint, and log snapshot is recorded in both `submit_result.vc_submission`
and `execution_surface.vc_runtime.resolved_submission`.
VC templates separate the model/image interpreter (`PYTHON_BIN`/`MODEL_PYTHON`)
from the harness-script interpreter (`HARNESS_PYTHON_BIN`). Set
`SURE_EVAL_HARNESS_PYTHON_BIN` or pass `--harness-python-bin` to
`run_vc_execution.py` when a mounted Python 3.11 environment should run harness
scripts without doing cold-start `pip --target` installs inside the vc job.

Local harness deployments may point at an existing runnable SURE-EVAL checkout without copying weights:

```bash
export LEGACY_SURE_EVAL_ROOT=/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox
python3 scripts/resolve_model_dir.py --model Qwen__Qwen3-ASR-1.7B --require-verdict --require-runtime-files
```

For production-style harness repos, prefer committing lightweight model artifacts under `sure/models/<model>/` and symlinking only heavy runtime directories (`.runtime/`, `checkpoints/`, optionally short-lived `.venv/`).

The standalone evaluation engine resolves to `sure/external/sure-evaluation` by
default. In a GitHub-backed harness worktree, this path should be a Git
submodule that points at the independent `sure-evaluation` repository. Use
`SURE_EVALUATION_HOME` or `--evaluation-engine-root` only as an explicit local
override; the workspace checkout is not an implicit fallback.
If `SURE_EVAL_CONFIG` is not supplied, harness runtime templates materialize
`<run_dir>/_harness_config.yaml` from the submodule's `config/default.yaml` and
rewrite results/cache paths to harness-local absolute paths. The default dataset
entry is `data/datasets` under the harness repository; local deployments should
symlink `data/datasets/sure_benchmark/jsonl` to the shared sandbox JSONL root:
`/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/data/datasets/sure_benchmark/jsonl`.
Override `SURE_EVAL_DATASETS_ROOT` only when pointing at another directory that
contains `sure_benchmark/jsonl`.

`evaluate_predictions.py` accepts `--evaluation-backend auto|external|legacy`
and `--strict-main-flow`. Harness main-flow templates default to
`EVALUATION_BACKEND=external` and `STRICT_MAIN_FLOW=1`; this prevents the
default path from falling back to the vendored legacy evaluator. Use `auto` or
`legacy` only for explicit local compatibility work, not for aligned main-flow
validation. Metric support is discovered from the current standalone
`sure-evaluation` engine at runtime; the harness must not maintain a separate
static support matrix.
For generated-audio tasks, pass an explicit audio metric when needed, for
example `--metric dnsmos` or `--evaluation-metric dnsmos`; the harness converts
structured TTS/VC predictions into the standalone engine's `samples_jsonl`
contract according to the selected route's required roles. Repeated `--metric`
values produce one dataset-metric result each, and `--merge-payload` merges
segmented TTS/VC evaluation payloads without rerunning metrics.

`run_model_mcp_smoke.py --device cpu` and
`generate_predictions_via_server.py --device cpu` hide `CUDA_VISIBLE_DEVICES`
unless the caller explicitly overrides it. This keeps CPU smoke runs from being
pulled back onto a busy or incompatible GPU by model-local runtimes.

## Gate Checks (enforced by hooks)

- `script_routing`: steps whitelisted, scripts under `scripts/`.
- `execution_readiness`: `execution_ready && isolation_audit.audit_passed`; `check_execution_surface_compliance.py` (red line 1) also writes `evaluation_route_plan.json` and blocks when standalone `sure-evaluation` reports selected route/node environment issues. The plan must include engine commit, supported/default metrics, selected metrics, route choices, selected routes, and setup commands for blocking node-local environments. Bounded smoke is enforced by the next `smoke_test` gate.
- `smoke_test`: `smoke_passed` true; entrypoint exists.
- `submit_vc_run`: `vc_check.py` enforces `execution=local|vc|auto` semantics against real `which vc && vc info` availability.
- `assessment`: anomaly → `user_confirmed` true.
- `run_report`: `report_persisted` true, `execution_path_actual` declared, and execution/device/sample provenance recorded. Completed runs should index `eval_input_resolved.json` and `evaluation_route_plan.json`, and must contain model-local `evaluation_payload.json`, `protocol.yaml`, `report.jsonl`, `metrics/<dataset>/<metric_slug>/{report.json,pipeline_description.json}`, `sample_reports/<dataset>/<metric_slug>.jsonl`, and `predictions/<dataset>.txt/.jsonl`.

On gate failure the hook blocks with a `repair` message and bumps the retry counter (max 3); beyond that the unit is marked FAILED — classify via `references/failure_taxonomy.md` and repair or finish with `status: failed`. Do not blind-retry.

## Success Criteria

The `pre_finish` hook enforces: `main_agent_run_report.json` exists, the terminal gate passes, and the state machine reached the terminal unit. On success call `sure_finish` with `status: "success"` and `manifest_path: ".sure/runs/<run_id>/manifest.json"`. If incomplete or blocked, finish with `status: "incomplete"` or `status: "failed"` and a repair summary.
