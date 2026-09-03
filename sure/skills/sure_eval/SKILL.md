---
name: sure-eval
description: Run reproducible inference from an approved model's sealed container or local Python binding, then execute the selected evaluation pipeline and stage complete results for review.
---

# /sure_eval

Orchestrate inference plus deterministic evaluation for a model that has been human-approved into a configured `approved_models_roots` directory. Container bindings mount approved storage read-only. A site-approved Python binding runs on a trusted host and verifies model-core hashes before and after execution. New evaluation products are staged under repository-local `sure/results` for later human promotion.

**Prerequisite**: run `/sure_init` first to select an agent, configure auth, and validate the environment for this project.

Control principle: **agent decides scope, scripts enforce format and execution.** You (the agent) choose which datasets, what target, how to route; the deterministic scripts under `scripts/` and the hook gates enforce that every artifact is in the right place, the right format, and the right value domain — and that execution-surface isolation plus the user-selected execution policy hold.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `model` | ✅ | Exact approved directory name below a configured `approved_models_roots` entry. No path, alias, environment-root, or local-model fallback is accepted. |
| `datasets` | ✅ | Comma-separated source paths below a configured `allowed_source_roots` entry, e.g. `datasets=/srv/sure/datasets/group/store/ds_pool/example@v1.0.2`. The strict main flow rejects legacy dataset names and short aliases; multi-version sources require the trailing `@<version_id>` (single-version sources may omit it). Dataset metadata, not a user-supplied task flag, determines ASR/TTS/VC/etc. |
| `datasets_root` | — | Absolute writable projection root for generated JSONL indexes and metadata. Resolution precedence is this parameter, `SURE_EVAL_DATASETS_ROOT`, `datasets.projection_root` in site policy, an explicit config's `data.datasets`, then the repository development default. It must stay outside forbidden output roots and must not overlap a source root. Raw data is referenced in place and is never copied or moved. |
| `protocol` | — | `standard_system` (default) follows the approved model's upstream configuration. `strict_core` requires every conservative parameter to be mapped to an MCP argument or explicitly proven not applicable. |
| `device` | — | `auto \| cpu \| cuda \| cuda:<index>`. Default `auto`; resolved by `scripts/resolve_eval_input.py` and handed to `scripts/infer_entrypoint.py` by `scripts/run_infer.py`. `cuda:<index>` selects the local host GPU by setting `CUDA_VISIBLE_DEVICES=<index>`. |
| `target` | — | Target metric or paper to compare against. |
| `max_samples` | — | Sample cap for bounded validation runs. Omitted or `0` means full dataset. |
| `execution` | — | `auto \| local`. Both resolve to the approved local runtime: `local_docker` for container bindings, `local_python` for approved Python runtimes. `vc` is no longer accepted. |
| `execution_path` | — | Legacy alias: `auto \| local_docker \| local_python`. `local_bash` is normalized to the approved local runtime; arbitrary host inference is forbidden. |
| `metrics` | — | Comma-separated reported metrics, e.g. `metrics=cer`. Selects the current default route per metric; use an exact `pipeline_id` via `/sure_reval` for route variants. |
| `config` | — | Explicit harness evaluation config path (otherwise materialized from the engine's `config/default.yaml`). |
| `evaluation_backend` | — | `external` (default) delegates metrics to the engine; `legacy` is compatibility/debug only. |
| `evaluation_engine_root` | — | Explicit engine checkout override, same role as `SURE_EVALUATION_HOME`. |
| `audit` | — | When true, triage existing results instead of running a new evaluation. |
| `run_id` | — | Resume a specific run. |
| `output_dir` | — | Absolute directory that becomes this invocation's product directory, replacing the repository-local `sure/results/<model>/<protocol>/<run_id>` staging path. The harness consumes it at `pre_start` and resolves it into the payload before the agent starts, so read `runtime.run_dir` rather than this parameter. It must be outside every configured `forbidden_output_roots` entry, creatable and writable. |

The invocation directory holds control artifacts under `.sure/runs/<run_id>/artifacts/`. The evaluation product directory recorded in `eval_input_resolved.json -> runtime.run_dir` is the source of truth for prediction, protocol, report, metric, and sample-level artifacts. It defaults to repository-local `sure/results/<model>/<protocol>/<run_id>`, and is the requested directory itself when the invocation passed `output_dir`. The `model_dir` parameter is forbidden.

At `pre_start`, the hook resolves the product input into
`<run_dir>/artifacts/eval_input_resolved.json` by running
`scripts/resolve_eval_input.py`. This artifact is the bridge from the
user-friendly `/sure_eval model=... datasets=... device=...` surface to the
plan every later unit reads:

- `model`, including the immutable deployment binding, comes only from the
  approved model directory.
- `datasets` is the canonical expanded dataset list; the evaluation task and
  language are inferred from dataset metadata.
- `runtime` records `run_dir`, the resolved device, the execution plan and the
  `max_samples` sample scope before the state machine starts.

Immediately after input resolution, `pre_start` runs
`scripts/preflight_evaluation_support.py`, which writes
`<run_dir>/artifacts/evaluation_preflight.json`. When the sure-evaluation
package has no route for a requested (task, language, metric), the artifact
records `supported: false`, the fixed reason code
`EVALUATION_PACKAGE_UNSUPPORTED`, and the fixed reason string, and the flow
stops before any unit runs — an unsupported route is terminal, so no retry is
attempted. When the engine is unavailable the preflight is skipped and later
stages report the engine problem themselves.

Do not ask the user for `task` as the primary input. If a legacy prompt includes
`task=asr` or similar, treat it only as a consistency hint; the source of truth
for evaluation task routing is the dataset metadata resolved into
`eval_input_resolved.json`.

## State Machine

Advance happens **only** when the current unit's `produces` artifact is compliant (location + format + value domain; no forbidden fields). Linear units are agent self-driven; gate units additionally run a Python semantic check. Produce the current unit's artifact, then call `sure_update_state`.

| # | Unit | Kind | Produces | Gate script |
|---|------|------|----------|-------------|
| 1 | `task_classification` | linear | `task_classification.json` | — |
| 2 | `tool_readiness_routing` | **gate** | `tool_readiness_routing.json` | — (in-process: blocks on `handoff_to_tool_agent=true`) |
| 3 | `plan` | linear | `main_agent_plan.json` | — |
| 4 | `dataset_scope` | linear | `dataset_decision.json` | — |
| 5 | `script_routing` | **gate** | `script_routing.json` | `scripts/check_script_routing.py` |
| 6 | `execution_surface` | **gate** | `execution_surface.json` | — (written by `scripts/run_infer.py`) |
| 7 | `execution_readiness` | **gate** | `execution_readiness_report.json` | `scripts/check_execution_surface_compliance.py` |
| 8 | `smoke_test` | **gate** | `smoke_test_result.json` | `scripts/run_smoke.py` |
| 9 | `submit_vc_run` | **gate** | `submit_result.json` | — |
| 10 | `execute_wait` | **gate** | `execution_result.json` | `scripts/check_execution_result.py` |
| 11 | `assessment` | **gate** | `assessment_report.json` | `scripts/check_assessment.py` |
| 12 | `extract_lessons` | **gate** | `extraction_declaration.json` | `scripts/check_memory_extraction.py` |
| 13 | `run_report` | **gate** | `main_agent_run_report.json` | `scripts/check_run_report.py` |

### Per-unit contract (Inputs → Output → Allowed → Must Not Do → Failure)

Each unit must satisfy: **Inputs** (previous unit's produces + evidence sources to read) → **Output** (`produces` JSON, schema in `schemas/`) → **Allowed** (value domain) → **Must Not Do** (forbidden fields that belong to later units — anti step-merge) → **Failure** classification.

- **task_classification**: Inputs = `eval_input_resolved.json` + exact NFS model. Output = `task_classification.json` {task_type, reason, need_tool_workflow, confidence, input_signals}. Allowed: task_type ∈ {onboarding_then_evaluate,evaluate_existing_model,repair_broken_model,audit_results}. Must Not Do: do not select datasets or set `execution_path`/`report_persisted` (later units); do not add memory fields to `task_classification.json` (its schema forbids extra keys). Also read `artifacts/memory_context.json` when it exists: the `pre_start` hook writes it with the memory facts that match this cluster, model and datasets, shape `{schema: "sure.memory.context.v1", skill, target_id, facts: [{entry_id, title, path, scope, checked_at, stale, status}], omitted_provisional}`; the file is written even when nothing matched (`facts: []`); it is advisory, verify before relying, and `stale: true` means the fact is older than its scope's re-check limit. Routing for the rest of the memory tree is `references/memory/ROUTING.md`.
- **tool_readiness_routing**: Inputs = exact approved NFS model with `artifacts/deployment_ready.json`, `runtime_inventory.json`, `package_gate.json`, and their declared hashes. Readiness requires either a `docker-registry`/digest-pinned/container-only binding, or a `package=none`/uv/content-addressed Python binding permitted by the active site. Python additionally requires model-core hashes and the matching site runtime. Missing or inconsistent fields route back to `/sure_onboard` and human promotion.
- **plan**: Inputs = task classification + tool readiness + `eval_input_resolved.json`. Output follows `main_agent_plan.schema.json` and describes execution order only.
- **dataset_scope**: Inputs = `eval_input_resolved.json` + explicit human constraints. Output = {selection_basis, selected_datasets, skipped_datasets}. User-provided datasets are validated/canonicalized here; this unit should not silently invent a different dataset scope.
- **script_routing**: Output steps[] each {name, script}. name ∈ the whitelist (see `schemas/script_routing.schema.json`); `script` must resolve under `scripts/`.
- **execution_surface**: run `scripts/run_infer.py --run-dir <sure_run_dir>`. It writes `execution_surface.json` from `scripts/infer_entrypoint.py` (entrypoint path, its sha256, the approved binding summary copied from `eval_input_resolved.json`), runs the compliance checks, launches inference in the approved runtime and writes `execution_result.json`. Do not author the surface JSON by hand, and do not emit later-unit fields.
- **execution_readiness**: `check_execution_surface_compliance.py` verifies the surface points at the bundled entrypoint (path + digest), that its binding matches the approved input (schema version, runtime kind, bundle identity, execution mode, model integrity policy, writable result policy, image for containers), rejects `local_bash`, unapproved `.venv`/host interpreters and tool mismatches, and live-probes the exact approved container or site Model Python. Any probe failure blocks: a node that cannot start the approved container, or a probe that came up and reported a broken runtime. The binding comparison here only catches a hand-edited surface; the cross-check against `eval_input_resolved.json` runs again at the `execute_wait` gate.
- **smoke_test**: the bounded smoke pass is the `smoke` stage of `scripts/infer_entrypoint.py`. `run_smoke.py` reads `execution_result.json`, writes `smoke_test_result.json`, and fails when inference stopped in or before that stage; `smoke_passed` true.
- **submit_vc_run**: a pass-through record of the local execution path (`local_docker` for containers, `local_python` for Python bindings) until the state machine is reshaped; `run_infer.py` writes `submit_result.json`. Neither route may infer or rewrite a model `.venv`.
- **execute_wait**: `check_execution_result.py` validates the terminal record (`job_status`, exit code, execution path against the surface plan, surface binding against the approved input) and, for a succeeded run, cross-checks the product tree (`predictions/<dataset>.txt` counts, `prediction_generation_status.json`, `protocol.yaml`, `references/sure_benchmark/jsonl/<dataset>.jsonl`). A terminal failure is a valid outcome that the run report must then state.
- **assessment**: {anomaly_detected, user_confirmed}. Anomaly (e.g. WER/CER > 50%, Accuracy < 20%) requires user confirmation.
- **extract_lessons**: Inputs = `artifacts/run_digest.json`, written by the hook the moment `assessment` passed (read it; never rebuild it in place). Output = `extraction_declaration.json` {schema, no_new_lessons, no_lessons_reason, covered_by, candidates, infra_noise, infra_evidence} plus 0 to 5 candidate directories under `artifacts/candidates/<nn>-<slug>/` (`proposal.json` + `proposal.md`) and, for facts, evidence files under `artifacts/memory_evidence/`. The full contract (digest fields, candidate formats, the gate's ten checks, the write-tools-only rule) is `sure/runtime/memory/EXTRACTION.md`; read it before writing anything. Write candidates and evidence first and the declaration last. `no_new_lessons: true` with a one-line reason is the normal result of a clean run. Must Not Do: do not run `scripts/build_run_digest.py` onto `artifacts/run_digest.json` (a preview goes to `--out <run_dir>/artifacts/run_digest.preview.json` and the gate ignores it); do not write under `sure/memory/` or `references/memory/`; do not use bash heredocs for these files. Failure: `scripts/check_memory_extraction.py` says which check failed; after two consecutive failures the hook advances on its own with `extraction: failed`, and switching to `no_new_lessons: true` with the reason is always a valid way out.
- **run_report**: {report_persisted, execution_path_actual}. Record `execution_path_requested`, `execution_path_actual`, `device_request`, `device_actual`, `max_samples`, total dataset samples, and evaluated samples.

## System Constraints (red lines — non-negotiable)

```
[SYSTEM_CONSTRAINT: EXECUTION_SURFACE_ISOLATION]
The execution surface (execution_surface.json) is generated, not authored:
1. GENERATED_SURFACE:
   - scripts/run_infer.py writes execution_surface.json from scripts/infer_entrypoint.py.
   - You MUST NOT write or edit execution_surface.json by hand.
   - You MUST NOT run any other entrypoint, and MUST NOT reference prior `eval_runs`.
2. ENTRYPOINT_DECLARATION:
   - execution_surface.json -> source_provenance.template_file, template_sha256 and
     entrypoint_path MUST name the bundled scripts/infer_entrypoint.py, byte for byte.
3. SELF_VERIFICATION:
   - Declare execution_ready=false if unsure. The execution_readiness gate runs
     scripts/check_execution_surface_compliance.py, which verifies the path + sha256
     and the approved runtime binding.

[SYSTEM_CONSTRAINT: EXECUTION_POLICY]
The user controls where formal model inference runs:
1. EXECUTION_REQUEST:
   - `execution=local`, `execution=auto`, or omitted: run the materialized surface through its approved `local_docker` or `local_python` binding. The site policy's `execution.local_runtimes` decides which runtime kinds are enabled.
   - `execution=vc` is rejected: remote job submission is no longer an execution surface.
2. DEVICE_REQUEST:
   - `device=cpu` hides `CUDA_VISIBLE_DEVICES`.
   - `device=cuda:<index>` records the user request, sets `CUDA_VISIBLE_DEVICES=<index>` for local execution, and records process-visible `device_actual=cuda:0`.
3. PROVENANCE:
   - `submit_result.json`, `execution_result.json`, `prediction_generation_status.json`, `protocol.yaml`, and `main_agent_run_report.json` record the exact image digest, execution location, device, and mount policy.
```

## Artifact Protocol

Generated prediction runs write `prediction_generation_status.json` with schema
`sure.eval.prediction_generation_status.v2`. The source of truth is what the
harness actually sent and where it executed:

- `runtime`: MCP server command, container working directory/Python, exact image ref, and `runtime_inventory.json` v2 summary. Local onboard Python remains evidence only.
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
`refresh_report_snapshot.py`, `run_infer.py`, `infer_entrypoint.py`,
`check_execution_surface_compliance.py`, `check_execution_result.py`) are the
routing targets. Run them as:

```bash
"$HARNESS_PYTHON_BIN" scripts/<script>.py <args>   # cwd = skill package dir
```

At `execution_surface`, call `scripts/run_infer.py --run-dir <sure_run_dir>`. It
writes `execution_surface.json`, runs the compliance checks, then launches
`scripts/infer_entrypoint.py` with the approved Harness Python inside the approved
container (`local_docker`) or on the trusted host (`local_python`), and writes
`execution_result.json` (plus, transitionally, `submit_result.json`).
All execution routes inject the Model Python and server command declared by
`runtime_inventory.json`, plus the independently resolved, versioned common
`HARNESS_PYTHON_BIN`. Container routes resolve both roles inside the image. Python
routes resolve the portable Model Runtime ID against the active site's
`storage.runtime_root`, pass a sanitized environment, redirect caches into the run
directory, and verify model-core hashes before and after execution. The two
executables are validated separately and must not silently collapse to the same
interpreter. Host model-interpreter overrides and `.venv` rewrites are rejected.
The standalone evaluator uses a third role, `SURE_EVALUATION_PYTHON`. Its root
dependencies are resolved from the versioned contract under
`sure/runtime/evaluation/`, cached by engine commit and lock hash under
`sure/.runtime/evaluation/`, and recorded in `evaluation_payload.json` and
`protocol.yaml`. It may be prepared online only
during evaluation readiness and only from the committed lock. Never install an
evaluation dependency into Harness Python or Model Python. Node-local evaluation
environments remain owned by the selected pipeline nodes, and a run never builds
one: no `uv venv`, no `uv sync`, no searching storage for a wheel. The per-node
`uv sync` in the metric READMEs is a maintainer instruction for preparing an
engine checkout, not a step inside a run. A missing node environment is a blocker
to report, not to repair inline — one run spent twenty minutes compiling a
normalization dependency, hit its own timeout, and produced nothing.

Report it like this. The engine names the missing node by its `node_id`; the
command that builds its environment is
`sure-eval env setup --node <node_id>`, run once per engine checkout by whoever
owns the checkout, never from inside a run — the environments live at
`<engine>/src/sure_eval/evaluation/nodes/<node_id>/.venv` and are gitignored, so
each checkout needs its own. Carry the blocker and the command into the run's
report and stop; do not run the command, and do not go looking for a wheel.

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
Input resolution always materializes `<run_dir>/_harness_config.yaml` from the
selected config and binds its dataset entry to the resolved writable projection
root. The first run creates `sure_benchmark/jsonl` plus generated indexes and
metadata there. Source `sample.jsonl`, `ds.jsonl`, and raw audio remain under the
configured `allowed_source_roots`; execution mounts those source roots and the
approved model read-only. Configure the projection with `datasets_root`,
`SURE_EVAL_DATASETS_ROOT`, or site policy `datasets.projection_root`. The
repository-local `data/datasets` path is only the development fallback.

`evaluate_predictions.py` accepts `--evaluation-backend auto|external|legacy`.
The evaluation runner passes `--evaluation-backend external` by default; this
prevents the default path from falling back to the vendored legacy evaluator. Use `auto` or
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

`run_smoke.py` launches nothing: it reads `execution_result.json` and fails when
`infer_entrypoint.py` stopped in or before its bounded `smoke` stage.
`generate_predictions_via_server.py --device cpu` hides
`CUDA_VISIBLE_DEVICES` in the selected runtime; it never falls back to an
unapproved host interpreter.

## Gate Checks (enforced by hooks)

- `script_routing`: steps whitelisted, scripts under `scripts/`.
- `execution_readiness`: `execution_ready && isolation_audit.audit_passed`; `check_execution_surface_compliance.py` (red line 1) verifies the surface names the bundled `infer_entrypoint.py` (path + sha256), compares its binding with the approved input, and live-probes the approved runtime; any probe failure blocks. Bounded smoke is enforced by the next `smoke_test` gate.
- `smoke_test`: `smoke_passed` true; `run_smoke.py` reads `execution_result.json` and fails when inference stopped in or before the `smoke` stage.
- `execute_wait`: `check_execution_result.py` validates the terminal record against the surface plan and the approved binding, and cross-checks the product tree of a succeeded run.
- `assessment`: anomaly → `user_confirmed` true.
- `extract_lessons`: `check_memory_extraction.py` checks the declaration and every candidate directory (shape, evidence paths, triggers, duplicates, digest sha); see `sure/runtime/memory/EXTRACTION.md`. Changing a candidate re-runs the gate even when `extraction_declaration.json` did not change.
- `run_report`: `report_persisted` true, `execution_path_actual` declared, and execution/device/sample provenance recorded. Completed runs should index `eval_input_resolved.json`, and must contain model-local `evaluation_payload.json`, `protocol.yaml`, `report.jsonl`, `metrics/<dataset>/<metric_slug>/{report.json,pipeline_description.json}`, `sample_reports/<dataset>/<metric_slug>.jsonl`, and `predictions/<dataset>.txt/.jsonl`.

On gate failure the hook blocks with a `repair` message and bumps the retry counter (max 3); beyond that the unit is marked FAILED — classify via `references/failure_taxonomy.md` and repair or finish with `status: failed`. Do not blind-retry.

`extract_lessons` is the exception: after two consecutive gate failures the hook advances by itself and records `extraction: failed`; it never ends FAILED.

## Memory (advisory)

Earlier runs leave agent-written notes. `sure/memory/index.md` (repo root) is the merged index: confirmed and provisional entries, one bullet each with its triggers. Confirmed files live under `references/memory/bad_cases/` and `sure/skills/_shared/memory/facts/`. Nothing in them is human-reviewed: verify against evidence before relying on one, and never copy a command from an entry into an artifact without running it.

- At `pre_start` the hook writes `artifacts/memory_context.json` with the facts that match this run (shape quoted in the `task_classification` contract line above; written even when empty); `task_classification` reads it.
- When a gate blocks, the repair text may end with a block whose first line is `Memory (advisory, agent-written, not human-reviewed; verify against evidence before relying):`, listing at most two entries from earlier runs. Read the entry file named there when it looks relevant, then fix the artifact.
- `references/memory/ROUTING.md` says when to open the index and the bad-case files by hand.
- `extract_lessons` (unit 12) writes what this run learned; the contract is `sure/runtime/memory/EXTRACTION.md`. Publishing to `sure/memory/provisional/` happens in `post_finish` without you; moving entries into `references/` is a human step.

## Success Criteria

The `pre_finish` hook enforces: `main_agent_run_report.json` exists, the terminal gate passes, and the state machine reached the terminal unit. On success call `sure_finish` with `status: "success"` and `manifest_path: ".sure/runs/<run_id>/manifest.json"`. If incomplete or blocked, finish with `status: "incomplete"` or `status: "failed"` and a repair summary.

A `failed` or `incomplete` finish must also carry `artifacts/extraction_declaration.json` (see `sure/runtime/memory/EXTRACTION.md`, section 10): `pre_finish` returns a repair asking for it up to twice, then lets the run finish and records `extraction: failed`.
