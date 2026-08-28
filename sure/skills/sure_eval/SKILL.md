---
name: sure-eval
description: Run reproducible inference from an approved Python or immutable container binding, then execute the selected evaluation pipeline and stage complete results for review.
---

# /sure_eval

Orchestrate inference plus deterministic evaluation for an audio model that has been human-approved into a configured `approved_models_roots` directory. Approved storage is read-only; new evaluation products are staged under repository-local `sure/results` for later human promotion.

A private distribution may carry an upstream main-flow mirror under `references/main_flow_agent/` for audit and parity review only. The public distribution omits that mirror. It is never a runtime template or execution source; adapt harness execution only under this skill package.

**Prerequisite**: run `/sure_init` first to select an agent, configure auth, and validate the environment for this project.

Control principle: **agent decides scope, scripts enforce format and execution.** You (the agent) choose which datasets, what target, how to route; the deterministic scripts under `scripts/` and the hook gates enforce that every artifact is in the right place, the right format, and the right value domain — and that execution-surface isolation plus the user-selected execution policy hold.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `model` | ✅ | Exact approved directory name below a configured `approved_models_roots` entry. No path, alias, environment-root, or local-model fallback is accepted. |
| `datasets` | ✅ | Comma-separated source paths below a configured `allowed_source_roots` entry, e.g. `datasets=/srv/sure/datasets/group/store/ds_pool/example@v1.0.2`. The strict main flow rejects legacy dataset names and short aliases; multi-version sources require the trailing `@<version_id>` (single-version sources may omit it). Dataset metadata, not a user-supplied task flag, determines ASR/TTS/VC/etc. |
| `protocol` | — | `standard_system` (default) follows the approved model's upstream configuration. `strict_core` requires every conservative parameter to be mapped to an MCP argument or explicitly proven not applicable. |
| `device` | — | `auto \| cpu \| cuda \| cuda:<index>`. Default `auto`; resolved by `scripts/resolve_eval_input.py` and passed through inference/evaluation templates when materialized. For `execution=local`, `cuda:<index>` selects the local host GPU by setting `CUDA_VISIBLE_DEVICES=<index>`. For `execution=vc`, the allocated container GPU is addressed as `cuda:0`; choose hardware with `vc_partition`/`vc_gpu`, not a host CUDA ordinal. |
| `target` | — | Target metric or paper to compare against. |
| `max_samples` | — | Sample cap for bounded validation runs. Omitted or `0` means full dataset. |
| `execution` | — | `auto \| local \| vc`. Default `auto`; `local` is an explicit user choice, `vc` requires a real vc submission, `auto` prefers vc when available. |
| `runtime` | — | `auto \| python \| container`. Default `auto` selects the approved model binding; an explicit value must match it. |
| `execution_path` | — | Legacy alias: `auto \| vc_submit \| local_python \| local_docker`. `local_bash` is normalized to the approved local runtime. |
| `vc_partition` / `vc_cpu` / `vc_mem` / `vc_gpu` | — | Optional VC resource overrides. The image cannot be overridden and always comes from the approved deployment binding. |
| `metrics` | — | Comma-separated reported metrics, e.g. `metrics=cer`. Selects the current default route per metric; use an exact `pipeline_id` via `/sure_reval` for route variants. |
| `vc_job_name` | — | Optional vc job name recorded with the submit result. |
| `config` | — | Explicit harness evaluation config path (otherwise materialized from the engine's `config/default.yaml`). |
| `evaluation_backend` | — | `external` (default) delegates metrics to the engine; `legacy` is compatibility/debug only. |
| `evaluation_engine_root` | — | Explicit engine checkout override, same role as `SURE_EVALUATION_HOME`. |
| `audit` | — | When true, triage existing results instead of running a new evaluation. |
| `run_id` | — | Resume a specific run. |
| `output_dir` | — | Absolute directory that becomes this invocation's product directory, replacing the repository-local `sure/results/<model>/<protocol>/<run_id>` staging path. The harness consumes it at `pre_start` and resolves it into the payload before the agent starts, so read `runtime.run_dir` rather than this parameter. It must be outside every configured `forbidden_output_roots` entry, creatable and writable; under `execution=vc` it must be on storage the container can mount. |
| `template` | — | Execution-surface template name under `scripts/templates/`. |

The invocation directory holds control artifacts under `.sure/runs/<run_id>/artifacts/`. The evaluation product directory recorded in `eval_input_resolved.json -> runtime.run_dir` is the source of truth for prediction, protocol, report, metric, and sample-level artifacts. It defaults to repository-local `sure/results/<model>/<protocol>/<run_id>`, and is the requested directory itself when the invocation passed `output_dir`. The `model_dir` parameter is forbidden.

At `pre_start`, the hook resolves the product input into
`<run_dir>/artifacts/eval_input_resolved.json` by running
`scripts/resolve_eval_input.py`. This artifact is the bridge from the
user-friendly `/sure_eval model=... datasets=... device=...` surface to the
upstream `MAIN_FLOW_INPUT` shape documented in
`references/contracts/main_agent_spec.md`:

- `target.model_name/model_dir/tool_workflow_ready/integration_state` and the
  immutable deployment binding come only from the approved NFS model directory.
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

- **task_classification**: Inputs = `eval_input_resolved.json` + exact NFS model. Output = `task_classification.json` {task_type, reason, need_tool_workflow, confidence, input_signals}. Allowed: task_type ∈ {onboarding_then_evaluate,evaluate_existing_model,repair_broken_model,audit_results}. Must Not Do: do not select datasets or set `execution_path`/`report_persisted` (later units).
- **tool_readiness_routing**: Inputs = exact approved NFS model with `artifacts/deployment_ready.json`, `runtime_inventory.json`, `package_gate.json`, and their declared hashes. Readiness requires either an approved Python binding from `package=none` or a digest-pinned container binding from `package=docker-registry`, with disabled fallbacks and a read-only model policy. Missing or inconsistent fields route back to `/sure_onboard` and human promotion.
- **plan**: Inputs = task classification + tool readiness + `eval_input_resolved.json`. Output follows `main_agent_plan.schema.json` and describes execution order only.
- **dataset_scope**: Inputs = `eval_input_resolved.json` + explicit human constraints. Output = {selection_basis, selected_datasets, skipped_datasets}. User-provided datasets are validated/canonicalized here; this unit should not silently invent a different dataset scope.
- **execution_surface** / **execute_wait**: produce the declared JSON; see `schemas/`. Do not emit later-unit fields.
- **script_routing**: Output steps[] each {name, script}. name ∈ the whitelist (see `schemas/script_routing.schema.json`); `script` must resolve under `scripts/`.
- **execution_surface**: Output {entrypoint_path or entrypoint, source_provenance.template_file, deployment_binding}. The script comes only from `scripts/templates/`. Copy the approved binding summary from `eval_input_resolved.json`: schema, runtime kind, model Python or image ref, bundle identity, read-only model mount, and writable result mount. Do not declare an unapproved interpreter or mutable image.
- **execution_readiness**: `check_execution_surface_compliance.py` compares the surface binding with the approved input, rejects `local_bash`, runtime changes, writable approved models, and tool mismatches, then live-probes the selected Python or container runtime. It also preserves template isolation and validates the standalone evaluation route plan.
- **smoke_test**: bounded smoke on a tiny slice using the approved local Python or Docker runtime; `smoke_passed` true.
- **submit_vc_run**: `execution=local` uses `local_python` or `local_docker` according to the approved binding. `execution=vc` requires a container binding and uses its image through `vc_submit`; container `auto` prefers VC when available, while Python is local-only.
- **assessment**: {anomaly_detected, user_confirmed}. Anomaly (e.g. WER/CER > 50%, Accuracy < 20%) requires user confirmation.
- **run_report**: {report_persisted, execution_path_actual}. Record `execution_path_requested`, `execution_path_actual`, `device_request`, `device_actual`, `max_samples`, total dataset samples, and evaluated samples. Non-vc paths are valid for explicit `execution=local`; auto local fallback requires a reason and, if vc was available, explicit fallback approval.

## System Constraints (red lines — non-negotiable)

```
[SYSTEM_CONSTRAINT: EXECUTION_SURFACE_ISOLATION]
When materializing the execution surface (run_evaluation.sh):
1. ALLOWED_TEMPLATE_ROOTS: "scripts/templates/"
   - The generated script MUST be derived ONLY from a template under this approved root.
   - Use `scripts/templates/` for harness-adapted executable templates.
   - Any distribution-provided audit mirror under `references/main_flow_agent/templates/` MUST NOT be used as a runtime template root.
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
   - `execution=local`: run the materialized surface through the approved `local_python` or `local_docker` binding.
   - `execution=vc`: submit through vc. If `which vc && vc info` fails, the run must fail instead of falling back.
   - `execution=auto` or omitted: prefer vc when available; otherwise local fallback is allowed and must record the reason.
2. DEVICE_REQUEST:
   - `device=cpu` hides `CUDA_VISIBLE_DEVICES`.
   - `device=cuda:<index>` records the user request, sets `CUDA_VISIBLE_DEVICES=<index>` for local execution, and records process-visible `device_actual=cuda:0`.
   - For `execution=vc`, `device=auto|cuda|cuda:<index>` resolves to the container-visible `cuda:0`; a nonzero requested ordinal is preserved in provenance with an explanatory note because VC physical GPU selection is controlled by `vc_partition`/`vc_gpu`.
3. PROVENANCE:
   - `submit_result.json`, `execution_result.json`, `prediction_generation_status.json`, `protocol.yaml`, and `main_agent_run_report.json` record the exact runtime identity, execution location, device, and mount policy. Container runs also record the image digest.
```

## Artifact Protocol

Generated prediction runs write `prediction_generation_status.json` with schema
`sure.eval.prediction_generation_status.v2`. The source of truth is what the
harness actually sent and where it executed:

- `runtime`: MCP server command, approved working directory/Model Python, optional exact image ref, and `runtime_inventory.json` v2 summary.
- `environment`: allowlisted safe env values, all env keys, redacted secret-key names, execution path, and device binding.
- `generation`: protocol resolver output, explicit `--tool-arg` values, argument key policy, and raw-response observation.
- `datasets`: per-dataset prediction file, structured prediction file, generation count, logs, and status.

`protocol.yaml` is inference-only. It must include `inference_parameters`,
`prediction_reuse`, and `provenance`. For normal `/sure_eval`, provenance points
to the current run's `prediction_generation_status.json`; for `/sure_reval`,
`prediction_reuse.enabled=true` and provenance points to the source inference
protocol/status/runtime inventory when available. `raw_response` is preserved in
`predictions/<dataset>.jsonl` as model-output evidence only and must not be used
to infer model hyperparameters.

Only `standard_system` and `strict_core` are valid protocol IDs. `standard_system`
is the default and applies no harness generation override; its resolution records
the approved `config.yaml` path and SHA256. `strict_core` injects the mapped
conservative values into the actual MCP tool arguments. Missing mappings,
unsupported parameters, resolver failures, and conflicting `--tool-arg` values
are terminal errors. A null mapping is allowed only with
`status=not_applicable` and a concrete architecture reason.

## Backend

The deterministic harness backend is bundled in `scripts/`. The package
`scripts/sure_eval/` holds the model, dataset, inference, report, and legacy
evaluation compatibility code. Formal metric execution should prefer the
standalone `sure-evaluation` engine when it is available. Flat scripts under
`scripts/` (`resolve_eval_input.py`, `resolve_model_dir.py`,
`resolve_evaluation_engine.py`,
`resolve_evaluation_route_plan.py`, `prepare_sure_dataset.py`,
`materialize_predictions_template.py`, `generate_predictions_via_server.py`,
`validate_prediction_files.py`, `evaluate_predictions.py`,
`refresh_report_snapshot.py`, `run_local_execution.py`, `run_vc_execution.py`,
`check_execution_surface_compliance.py`) are the routing targets. Templates live
in `scripts/templates/`. Run them as:

```bash
"$HARNESS_PYTHON_BIN" scripts/<script>.py <args>   # cwd = skill package dir
```

For `execution=local`, call `scripts/run_local_execution.py --run-dir <sure_run_dir>`
from the submit unit. It runs `run_evaluation.sh` through the approved Python or container binding and writes
both `submit_result.json` and `execution_result.json`. For `execution=vc`, use
`scripts/run_vc_execution.py --run-dir <sure_run_dir>` from the submit unit. It
writes `submit_result.json`, includes the exact `vc submit` command and a
persistent `<sure_run_dir>/vc_logs/job.log`, and leaves final
`execution_result.json` to the following `execute_wait` unit. When vc resources
are selected at submit time, the effective digest-pinned image, partition, CPU,
GPU, memory, entrypoint, and log snapshot is recorded in both `submit_result.vc_submission`
and `execution_surface.vc_runtime.resolved_submission`.
All execution paths inject the Model Python and server command declared by
`runtime_inventory.json`, plus the independently resolved, versioned common
`HARNESS_PYTHON_BIN`. The two executables are validated separately and must not
silently collapse to the same interpreter. Unapproved interpreter overrides and
runtime rewrites are rejected.
The standalone evaluator uses a third role, `SURE_EVALUATION_PYTHON`. Its root
dependencies are resolved from the versioned contract under
`sure/runtime/evaluation/`, cached by engine commit and lock hash under
`sure/.runtime/evaluation/`, and recorded in `evaluation_route_plan.json`,
`evaluation_payload.json`, and `protocol.yaml`. It may be prepared online only
during evaluation readiness and only from the committed lock. Never install an
evaluation dependency into Harness Python or Model Python. Node-local evaluation
environments remain owned by the selected pipeline nodes.

Resolve an approved model with:

```bash
"$HARNESS_PYTHON_BIN" scripts/resolve_model_dir.py --model Qwen__Qwen3-ASR-1.7B --require-verdict --require-runtime-files
```

`sure/models/<model>` is an onboarding staging product and is never an eval input. An operator promotes verified models into NFS.

The standalone evaluation engine resolves to `sure/external/sure-evaluation` by
default. In a GitHub-backed harness worktree, this path should be a Git
submodule that points at the independent `sure-evaluation` repository. Use
`SURE_EVALUATION_HOME` or `--evaluation-engine-root` only as an explicit local
override; the workspace checkout is not an implicit fallback.
If `SURE_EVAL_CONFIG` is not supplied, harness runtime templates materialize
`<run_dir>/_harness_config.yaml` from the submodule's `config/default.yaml` and
rewrite results/cache paths to harness-local absolute paths. The default dataset
entry is `data/datasets` under the harness repository; deployments either
download and convert benchmark data with the repository scripts (see the user
guide's Benchmark Data section) or symlink an existing
`sure_benchmark/jsonl` tree into `data/datasets/sure_benchmark/jsonl`.
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

`run_smoke.py` launches the approved local Python or container runtime with a bounded sample.
`generate_predictions_via_server.py --device cpu` hides `CUDA_VISIBLE_DEVICES`
inside the selected runtime and verifies its identity before starting the server.

## Gate Checks (enforced by hooks)

- `script_routing`: steps whitelisted, scripts under `scripts/`.
- `execution_readiness`: `execution_ready && isolation_audit.audit_passed`; `check_execution_surface_compliance.py` (red line 1) also prepares/verifies the locked Evaluation Python, writes `evaluation_route_plan.json`, and blocks when standalone `sure-evaluation` reports root or selected node environment issues. The plan must include engine commit, Evaluation Runtime ID/lock, supported/default metrics, selected metrics, route choices, selected routes, and setup commands for blocking node-local environments. Bounded smoke is enforced by the next `smoke_test` gate.
- `smoke_test`: `smoke_passed` true; entrypoint exists.
- `submit_vc_run`: `vc_check.py` enforces `execution=local|vc|auto` semantics against real `which vc && vc info` availability.
- `assessment`: anomaly → `user_confirmed` true.
- `run_report`: `report_persisted` true, `execution_path_actual` declared, and execution/device/sample provenance recorded. Completed runs should index `eval_input_resolved.json` and `evaluation_route_plan.json`, and must contain model-local `evaluation_payload.json`, `protocol.yaml`, `report.jsonl`, `metrics/<dataset>/<metric_slug>/{report.json,pipeline_description.json}`, `sample_reports/<dataset>/<metric_slug>.jsonl`, and `predictions/<dataset>.txt/.jsonl`.

On gate failure the hook blocks with a `repair` message and bumps the retry counter (max 3); beyond that the unit is marked FAILED — classify via `references/failure_taxonomy.md` and repair or finish with `status: failed`. Do not blind-retry.

## Success Criteria

The `pre_finish` hook enforces: `main_agent_run_report.json` exists, the terminal gate passes, and the state machine reached the terminal unit. On success call `sure_finish` with `status: "success"` and `manifest_path: ".sure/runs/<run_id>/manifest.json"`. If incomplete or blocked, finish with `status: "incomplete"` or `status: "failed"` and a repair summary.
