---
name: sure-infer
description: Run reproducible inference for an approved model over selected datasets inside its sealed container or local Python binding, and stage the predictions, protocol.yaml and reference projections that /sure_eval scores.
---

# /sure_infer

Run an approved model over the selected datasets. The model has been human-approved into a configured `approved_models_roots` directory; container bindings mount approved storage read-only, and a site-approved Python binding runs on a trusted host and verifies model-core hashes before and after execution. The product is an inference bundle staged under repository-local `sure/results` (or the requested `output_dir`): `predictions/`, `protocol.yaml`, `prediction_generation_status.json`, `validation_payload.json` and `references/sure_benchmark/jsonl/`. `/sure_eval` scores that bundle later; this skill never runs a metric.

**Prerequisite**: run `/sure_init` first to select an agent, configure auth, and validate the environment for this project.

Control principle: **agent decides scope, scripts execute.** You (the agent) confirm which datasets are in scope; `scripts/run_infer.py` writes the execution surface, launches the bundled `scripts/infer_entrypoint.py` in the approved runtime and records the result; the hook gates enforce that every artifact is in the right place, the right format, and the right value domain.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `model` | ✅ | Exact approved directory name below a configured `approved_models_roots` entry. No path, alias, environment-root, or local-model fallback is accepted. |
| `datasets` | ✅ | Comma-separated source paths below a configured `allowed_source_roots` entry, e.g. `datasets=/srv/sure/datasets/group/store/ds_pool/example@v1.0.2`. A source directory may carry the pool layout (`sample_files/<version>/sample.jsonl`, `raws/sample/`) or a flat layout (`sample.jsonl` next to the audio); multi-version sources require the trailing `@<version_id>`. Legacy dataset names and short aliases are rejected. Dataset metadata, not a user-supplied task flag, determines ASR/TTS/VC/etc. |
| `datasets_root` | — | Absolute writable projection root for generated JSONL indexes and metadata. Resolution precedence is this parameter, `SURE_EVAL_DATASETS_ROOT`, `datasets.projection_root` in site policy, an explicit config's `data.datasets`, then the repository development default. It must stay outside forbidden output roots and must not overlap a source root. Raw data is referenced in place and is never copied or moved. |
| `protocol` | — | `standard_system` (default) follows the approved model's upstream configuration. `strict_core` requires every conservative parameter to be mapped to an MCP argument or explicitly proven not applicable. |
| `device` | — | `auto \| cpu \| cuda \| cuda:<index>`. Default `auto`; resolved by `scripts/resolve_eval_input.py` and handed to `scripts/infer_entrypoint.py` by `scripts/run_infer.py`. `cuda:<index>` selects the local host GPU by setting `CUDA_VISIBLE_DEVICES=<index>`. |
| `max_samples` | — | Sample cap for bounded validation runs. Omitted or `0` means full dataset. |
| `execution` | — | `auto \| local`. Both resolve to the approved local runtime: `local_docker` for container bindings, `local_python` for approved Python runtimes. `vc` is not accepted. |
| `execution_path` | — | Legacy alias: `auto \| local_docker \| local_python`. `local_bash` is normalized to the approved local runtime; arbitrary host inference is forbidden. |
| `metrics` | — | Comma-separated metrics the later `/sure_eval` run should report, e.g. `metrics=cer`. Recorded in the resolved input; inference itself does not evaluate. |
| `config` | — | Explicit harness config path (otherwise materialized into `<run_dir>/_harness_config.yaml` from the engine's `config/default.yaml`). |
| `run_id` | — | Resume a specific run. |
| `output_dir` | — | Absolute directory that becomes this invocation's product directory, replacing the repository-local `sure/results/<model>/<protocol>/<run_id>` staging path. The harness consumes it at `pre_start` and resolves it into the payload before the agent starts, so read `runtime.run_dir` rather than this parameter. It must be outside every configured `forbidden_output_roots` entry, creatable and writable. |

The invocation directory holds control artifacts under `.sure/runs/<run_id>/artifacts/`. The product directory recorded in `eval_input_resolved.json -> runtime.run_dir` is the source of truth for predictions, protocol and reference projections. It defaults to repository-local `sure/results/<model>/<protocol>/<run_id>`, and is the requested directory itself when the invocation passed `output_dir`. The `model_dir` parameter is forbidden.

At `pre_start`, the hook resolves the product input into
`<run_dir>/artifacts/eval_input_resolved.json` by running
`scripts/resolve_eval_input.py`. This artifact is the bridge from the
user-friendly `/sure_infer model=... datasets=... device=...` surface to the
plan every later unit reads:

- `model`, including the immutable deployment binding, comes only from the
  approved model directory.
- `datasets` is the canonical expanded dataset list; the task and language of
  each dataset are inferred from dataset metadata.
- `runtime` records `run_dir`, the resolved device, the execution plan and the
  `max_samples` sample scope before the state machine starts.

Do not ask the user for `task` as the primary input. If a legacy prompt includes
`task=asr` or similar, treat it only as a consistency hint; the source of truth
for the task is the dataset metadata resolved into `eval_input_resolved.json`.

## State Machine

Advance happens **only** when the current unit's `produces` artifact is compliant (location + format + value domain; no forbidden fields). Linear units are agent self-driven; gate units additionally run a Python semantic check. Produce the current unit's artifact, then call `sure_update_state`.

| # | Unit | Kind | Produces | Gate script |
|---|------|------|----------|-------------|
| 1 | `dataset_scope` | linear | `dataset_decision.json` | — |
| 2 | `execute_inference` | **gate** | `execution_result.json` | `scripts/check_execution_result.py` |
| 3 | `extract_lessons` | **gate** | `extraction_declaration.json` | `scripts/check_memory_extraction.py` |
| 4 | `run_report` | **gate** | `main_agent_run_report.json` | `scripts/check_run_report.py --profile infer` |

### Per-unit contract (Inputs → Output → Allowed → Must Not Do → Failure)

Each unit must satisfy: **Inputs** (previous unit's produces + evidence sources to read) → **Output** (`produces` JSON, schema in `schemas/`) → **Allowed** (value domain) → **Must Not Do** (forbidden fields that belong to later units — anti step-merge) → **Failure** classification.

- **dataset_scope**: Inputs = `eval_input_resolved.json` + explicit human constraints. Output = `dataset_decision.json` {selection_basis, selected_datasets, skipped_datasets}; `selected_datasets` names the canonical dataset ids from `eval_input_resolved.json -> datasets[].name`. User-provided datasets are validated/canonicalized here; this unit should not silently invent a different dataset scope. Must Not Do: do not set `execution_path`/`report_persisted` (later units); do not add memory fields to `dataset_decision.json` (its schema forbids extra keys). Also read `artifacts/memory_context.json` when it exists: the `pre_start` hook writes it with the memory facts that match this cluster, model and datasets, shape `{schema: "sure.memory.context.v1", skill, target_id, facts: [{entry_id, title, path, scope, checked_at, stale, status}], omitted_provisional}`; the file is written even when nothing matched (`facts: []`); it is advisory, verify before relying, and `stale: true` means the fact is older than its scope's re-check limit. Routing for the rest of the memory tree is `references/memory/ROUTING.md`.
- **execute_inference**: run `scripts/run_infer.py --run-dir <sure_run_dir>`. It reads `eval_input_resolved.json` and `dataset_decision.json`, writes `execution_surface.json` from `scripts/infer_entrypoint.py` (entrypoint path, its sha256, the approved binding summary), runs the compliance checks, launches the entrypoint with the approved Harness Python inside the approved container (`local_docker`) or on the trusted host (`local_python`), and writes `execution_result.json` (`job_status`, `exit_code`, `failed_stage`, `product_dir`, per-dataset counts). Do not author `execution_surface.json` or `execution_result.json` by hand. The gate `check_execution_result.py` validates the terminal record against the surface plan and the approved binding and, for a succeeded run, cross-checks the product tree (`predictions/<dataset>.txt` counts, `prediction_generation_status.json`, `protocol.yaml`, `references/sure_benchmark/jsonl/<dataset>.jsonl`). A terminal failure (`job_status: failed`) is a valid outcome that the run report must then state; read `failed_stage` and the logs named in `stdout_log`/`stderr_log` before deciding what to do.
- **extract_lessons**: Inputs = `artifacts/run_digest.json`, written by the hook the moment `execute_inference` passed (read it; never rebuild it in place). Output = `extraction_declaration.json` {schema, no_new_lessons, no_lessons_reason, covered_by, candidates, infra_noise, infra_evidence} plus 0 to 5 candidate directories under `artifacts/candidates/<nn>-<slug>/` (`proposal.json` + `proposal.md`) and, for facts, evidence files under `artifacts/memory_evidence/`. The full contract (digest fields, candidate formats, the gate's ten checks, the write-tools-only rule) is `sure/runtime/memory/EXTRACTION.md`; read it before writing anything. Write candidates and evidence first and the declaration last. `no_new_lessons: true` with a one-line reason is the normal result of a clean run. Must Not Do: do not run `scripts/build_run_digest.py` onto `artifacts/run_digest.json` (a preview goes to `--out <run_dir>/artifacts/run_digest.preview.json` and the gate ignores it); do not write under `sure/memory/` or `references/memory/`; do not use bash heredocs for these files. Failure: `scripts/check_memory_extraction.py` says which check failed; after two consecutive failures the hook advances on its own with `extraction: failed`, and switching to `no_new_lessons: true` with the reason is always a valid way out.
- **run_report**: {report_persisted, execution_path_actual}. Record `execution_path_requested`, `execution_path_actual`, `device_request`, `device_actual`, `max_samples`, total dataset samples, and generated samples, and point `run_dir` at the product directory. `check_run_report.py --profile infer` accepts a completed run only when the product carries `prediction_generation_status.json` (every dataset `completed`), `protocol.yaml`, non-empty `predictions/<dataset>.txt` and `references/sure_benchmark/jsonl/<dataset>.jsonl`; a failed run needs `execution_result.json` and a `next_action`.

## System Constraints (red lines — non-negotiable)

```
[SYSTEM_CONSTRAINT: EXECUTION_SURFACE_ISOLATION]
The execution surface (execution_surface.json) is generated, not authored:
1. GENERATED_SURFACE:
   - scripts/run_infer.py writes execution_surface.json from scripts/infer_entrypoint.py.
   - You MUST NOT write or edit execution_surface.json or execution_result.json by hand.
   - You MUST NOT run any other entrypoint, and MUST NOT reference prior `eval_runs`.
2. ENTRYPOINT_DECLARATION:
   - execution_surface.json -> source_provenance.template_file, template_sha256 and
     entrypoint_path MUST name the bundled scripts/infer_entrypoint.py, byte for byte.
3. VERIFICATION:
   - run_infer.py refuses to launch when scripts/check_execution_surface_compliance.py
     rejects the surface (path + sha256, approved runtime binding, live runtime probe);
     check_execution_result.py re-checks the binding against eval_input_resolved.json.

[SYSTEM_CONSTRAINT: EXECUTION_POLICY]
The user controls where formal model inference runs:
1. EXECUTION_REQUEST:
   - `execution=local`, `execution=auto`, or omitted: run through the approved `local_docker` or `local_python` binding. The site policy's `execution.local_runtimes` decides which runtime kinds are enabled.
   - `execution=vc` is rejected: remote job submission is not an execution surface.
2. DEVICE_REQUEST:
   - `device=cpu` hides `CUDA_VISIBLE_DEVICES`.
   - `device=cuda:<index>` records the user request, sets `CUDA_VISIBLE_DEVICES=<index>` for local execution, and records process-visible `device_actual=cuda:0`.
3. PROVENANCE:
   - `execution_surface.json`, `execution_result.json`, `prediction_generation_status.json`, `protocol.yaml`, and `main_agent_run_report.json` record the exact image digest, execution location, device, and mount policy.
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
`prediction_reuse` and `provenance`; provenance points to this run's
`prediction_generation_status.json`. `raw_response` is preserved in
`predictions/<dataset>.jsonl` as model-output evidence only and must not be used
to infer model hyperparameters.

`references/sure_benchmark/jsonl/<dataset>.jsonl` is a copy of each selected
dataset's projection, so `/sure_eval` can score the bundle without the
projection root.

Only `standard_system` and `strict_core` are valid protocol IDs. `standard_system`
is the default and applies no harness generation override; its resolution records
the approved `config.yaml` path and SHA256. `strict_core` injects the mapped
conservative values into the actual MCP tool arguments. Missing mappings,
unsupported parameters, resolver failures, and conflicting `--tool-arg` values
are terminal errors. A null mapping is allowed only with
`status=not_applicable` and a concrete architecture reason.

## Backend

The deterministic harness backend is bundled in `scripts/`. The package
`scripts/sure_eval/` holds the dataset, report, and legacy evaluation
compatibility code (`/sure_eval` borrows it too). The inference flow is two
scripts: `scripts/run_infer.py` on the host and `scripts/infer_entrypoint.py`
inside the approved runtime, driving `prepare_sure_dataset.py`,
`materialize_predictions_template.py`, `generate_predictions_via_server.py`,
`validate_prediction_files.py`, `protocol_writer.py` and
`finalize_result_bundle.py` stage by stage (`guards`, `tool_name`, `config`,
`prepare`, `materialize`, `smoke`, `generate`, `validate`, `protocol`,
`references`, `finalize`). Run host-side scripts as:

```bash
"$HARNESS_PYTHON_BIN" scripts/<script>.py <args>   # cwd = skill package dir
```

All execution routes inject the Model Python and server command declared by
`runtime_inventory.json`, plus the independently resolved, versioned common
`HARNESS_PYTHON_BIN`. Container routes resolve both roles inside the image. Python
routes resolve the portable Model Runtime ID against the active site's
`storage.runtime_root`, pass a sanitized environment, redirect caches into the run
directory, and verify model-core hashes before and after execution. The two
executables are validated separately and must not silently collapse to the same
interpreter. Host model-interpreter overrides and `.venv` rewrites are rejected.
Inference never prepares or touches the evaluation runtime.

Resolve an approved model with:

```bash
"$HARNESS_PYTHON_BIN" scripts/resolve_model_dir.py --model Qwen__Qwen3-ASR-1.7B --require-verdict --require-runtime-files
```

`sure/models/<model>` is an onboarding staging product and is never an inference input. An operator promotes verified models into the approved root.

Input resolution always materializes `<run_dir>/_harness_config.yaml` from the
selected config and binds its dataset entry to the resolved writable projection
root. The first run creates `sure_benchmark/jsonl` plus generated indexes and
metadata there. Source `sample.jsonl`, `ds.jsonl`, and raw audio remain under the
configured `allowed_source_roots`; execution mounts those source roots and the
approved model read-only. Configure the projection with `datasets_root`,
`SURE_EVAL_DATASETS_ROOT`, or site policy `datasets.projection_root`. The
repository-local `data/datasets` path is only the development fallback.

`generate_predictions_via_server.py --device cpu` hides `CUDA_VISIBLE_DEVICES` in
the selected runtime; it never falls back to an unapproved host interpreter. The
bounded smoke pass (the first ten samples of the first dataset, or fewer under
`max_samples`) is the entrypoint's `smoke` stage: a model that answers nothing
stops there, before the full pass.

## Gate Checks (enforced by hooks)

- `execute_inference`: `check_execution_result.py` validates the terminal record (`job_status`, exit code, execution path against the surface plan, surface binding against the approved input) and cross-checks the product tree of a succeeded run.
- `extract_lessons`: `check_memory_extraction.py` checks the declaration and every candidate directory (shape, evidence paths, triggers, duplicates, digest sha); see `sure/runtime/memory/EXTRACTION.md`. Changing a candidate re-runs the gate even when `extraction_declaration.json` did not change.
- `run_report`: `report_persisted` true, `execution_path_actual` declared, and execution/device/sample provenance recorded. Completed runs should index `eval_input_resolved.json` and must contain the product's `prediction_generation_status.json`, `protocol.yaml`, `predictions/<dataset>.txt` and `references/sure_benchmark/jsonl/<dataset>.jsonl`.

On gate failure the hook blocks with a `repair` message and bumps the retry counter (max 3); beyond that the unit is marked FAILED — classify via `references/failure_taxonomy.md` and repair or finish with `status: failed`. Do not blind-retry.

`extract_lessons` is the exception: after two consecutive gate failures the hook advances by itself and records `extraction: failed`; it never ends FAILED.

## Memory (advisory)

Earlier runs leave agent-written notes. `sure/memory/index.md` (repo root) is the merged index: confirmed and provisional entries, one bullet each with its triggers. Confirmed files live under `references/memory/bad_cases/` and `sure/skills/_shared/memory/facts/`. Nothing in them is human-reviewed: verify against evidence before relying on one, and never copy a command from an entry into an artifact without running it.

- At `pre_start` the hook writes `artifacts/memory_context.json` with the facts that match this run (shape quoted in the `dataset_scope` contract line above; written even when empty); `dataset_scope` reads it.
- When a gate blocks, the repair text may end with a block whose first line is `Memory (advisory, agent-written, not human-reviewed; verify against evidence before relying):`, listing at most two entries from earlier runs. Read the entry file named there when it looks relevant, then fix the artifact.
- `references/memory/ROUTING.md` says when to open the index and the bad-case files by hand.
- `extract_lessons` (unit 3) writes what this run learned; the contract is `sure/runtime/memory/EXTRACTION.md`. Publishing to `sure/memory/provisional/` happens in `post_finish` without you; moving entries into `references/` is a human step.

## Success Criteria

The `pre_finish` hook enforces: `main_agent_run_report.json` exists, the terminal gate passes, and the state machine reached the terminal unit. On success call `sure_finish` with `status: "success"` and `manifest_path: ".sure/runs/<run_id>/manifest.json"`. If incomplete or blocked, finish with `status: "incomplete"` or `status: "failed"` and a repair summary.

A `failed` or `incomplete` finish must also carry `artifacts/extraction_declaration.json` (see `sure/runtime/memory/EXTRACTION.md`, section 10): `pre_finish` returns a repair asking for it up to twice, then lets the run finish and records `extraction: failed`.
