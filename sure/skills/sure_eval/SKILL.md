---
name: sure-eval
description: Score an existing SURE prediction bundle (a local /sure_infer run or an approved result) with the pinned sure-evaluation engine, by metric or exact pipeline_id, without running model inference.
---

# /sure_eval

Score predictions that already exist. This skill is evaluation-only: it must not start a model server, invoke an MCP model tool, generate predictions, or reuse old evaluation scores. The product is an evaluation batch appended into the prediction bundle itself (`evaluation_runs/<batch-id>/`, aggregate `report.jsonl` and `report_snapshot.md`).

**Prerequisite**: run `/sure_init` first to select an agent, configure auth, and validate the environment for this project.

Control principle: **agent decides scope, scripts execute.** You (the agent) confirm which datasets of the source bundle are in scope; `../sure_infer/scripts/run_eval.py` resolves the routes, runs the pinned evaluation engine in the locked Evaluation Runtime and appends the batch; the hook gates enforce that every artifact is in the right place, the right format, and the right value domain.

## Prediction Sources

The `pre_start` hook resolves the source into `artifacts/prediction_source_resolved.json` by running `../sure_infer/scripts/resolve_prediction_source.py`. Two kinds exist:

| `source_kind` | Where the predictions come from | Where the batch is written |
|---------------|--------------------------------|----------------------------|
| `local_infer_run` | A `/sure_infer` bundle: `source=<run_id>` below `sure/results/<model>/<protocol>/`, `source=<absolute dir>`, or, when `source` is omitted, the unique run below `sure/results/<model>/<protocol>/` whose completed dataset set equals `datasets`. The bundle must carry `protocol.yaml`, `prediction_generation_status.json` (every dataset `completed`), `predictions/<dataset>.txt` and `references/sure_benchmark/jsonl/`. | In place: `staging_result_dir == source_results_dir`; there is no approved base report. |
| `approved_nfs_results` | An exact approved result below a configured `approved_results_roots` entry (used when no local run matches). | An append-only mirror below `sure/results/<same relative path>`; the first run copies the complete approved result as its baseline. |

Either way the model must be an approved model (`config.yaml` plus a successful verdict below `approved_models_roots`); its fingerprint, the protocol, the sorted dataset set and the prediction hashes form the source identity that every later artifact must repeat. `/sure_eval` never reads a harness-local, sandbox or environment-overridden reference root: the references come from the bundle. A bundle without `references/sure_benchmark/jsonl` stops with `INPUT_EVIDENCE_MISSING`.

At `pre_start` the hook also writes `artifacts/runtime_binding.json`: the exact Harness Runtime and locked Evaluation Runtime (IDs, lock hashes, executables, engine commit, cross-binding). Model Runtime is `required=false`; running any model environment is a contract violation.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `model` | ✅ | Exact approved model directory name. No aliases or paths. |
| `datasets` | ✅ | Complete comma-separated dataset set of the source, each item `<dataset_name>__<version_id>` (a flat source is `<name>__unversioned`). Subsets and supersets are rejected. |
| `source` | — | A `/sure_infer` run id below `sure/results/<model>/<protocol>/` or an absolute bundle directory. Omitted: the unique matching local run, else the approved result. |
| `pipeline_id` | one of | Comma-separated exact sure-evaluation pipeline IDs (route variants). |
| `metrics` | one of | Comma-separated metrics, e.g. `metrics=cer`; each is resolved to the engine's default pipeline for the dataset's task and language before anything runs, and the resolved ids are recorded in `pipeline_ids`. Exactly one of `pipeline_id` / `metrics` must be given. |
| `protocol` | — | `standard_system` (default) or `strict_core`; must equal the source bundle's protocol. |
| `device` | — | Evaluation device (`cpu` default). Never changes prediction identity. |
| `output_dir` | — | Absolute directory where the harness collects this invocation's `result.json` and control artifacts. Consumed at `pre_start`; the evaluation batch itself always lands in the source bundle. Must be outside every configured `forbidden_output_roots` entry and writable. |

`reuse_predictions_from`, `model_dir`, `tmp_root`, `copy_mode`, `max_samples`, `config` and `evaluation_engine_root` are rejected: they would weaken source identity, replace the pinned evaluator, or turn a full evaluation into a bounded test.

Example:

```text
/sure_eval model=Qwen__Qwen3-ASR-1.7B datasets=aishell1__v1.0.2 source=sure_infer_20260903_101500 metrics=cer
```

## State Machine

Advance happens **only** when the current unit's `produces` artifact is compliant (location + format + value domain; no forbidden fields). Linear units are agent self-driven; gate units additionally run a Python semantic check. Produce the current unit's artifact, then call `sure_update_state`.

| # | Unit | Kind | Produces | Gate script |
|---|------|------|----------|-------------|
| 1 | `dataset_scope` | linear | `dataset_decision.json` | — |
| 2 | `execute_evaluation` | **gate** | `eval_run_report.json` | `scripts/check_eval_run_report.py` |
| 3 | `assessment` | **gate** | `assessment_report.json` | `scripts/check_assessment.py` |
| 4 | `extract_lessons` | **gate** | `extraction_declaration.json` | `scripts/check_memory_extraction.py` |
| 5 | `run_report` | **gate** | `main_agent_run_report.json` | `scripts/check_run_report.py --profile eval` |

### Per-unit contract (Inputs → Output → Allowed → Must Not Do → Failure)

- **dataset_scope**: Inputs = `artifacts/prediction_source_resolved.json` + explicit human constraints. Output = `dataset_decision.json` {selection_basis, selected_datasets, skipped_datasets}; `selected_datasets` names the canonical dataset ids from `prediction_source_resolved.json -> datasets`. The source is scored as a whole: this unit confirms the set, it does not pick a subset or invent a different scope. Must Not Do: do not set `execution_path`/`report_persisted` (later units); do not add memory fields to `dataset_decision.json` (its schema forbids extra keys). Also read `artifacts/memory_context.json` when it exists: the `pre_start` hook writes it with the memory facts that match this cluster, model and datasets, shape `{schema: "sure.memory.context.v1", skill, target_id, facts: [{entry_id, title, path, scope, checked_at, stale, status}], omitted_provisional}`; the file is written even when nothing matched (`facts: []`); it is advisory, verify before relying, and `stale: true` means the fact is older than its scope's re-check limit. Routing for the rest of the memory tree is `../sure_infer/references/memory/ROUTING.md` (this package has no `references/` tree of its own).
- **execute_evaluation**: run the backend command below. It re-resolves the source, imports the predictions into `<sure_run_dir>/scratch/`, validates them, resolves the routes, runs the evaluation engine, persists the batch and writes `eval_run_report.json` into `artifacts/`. Do not author `eval_run_report.json` by hand. The gate `check_eval_run_report.py` validates the report against `prediction_source_resolved.json`, the scratch artifacts, the pinned engine commit and tree hash, the batch manifest and the appended `report.jsonl` rows. A backend failure leaves an `eval_run_report.json` with `status: failed` and an `error_code`; read it and the scratch evidence before deciding what to do.
- **assessment**: Inputs = the batch's metric artifacts and `report_snapshot.md`. Output = `assessment_report.json` {anomaly_detected, user_confirmed, ...}: say whether the scores look plausible for this model and dataset set, name the anomaly when there is one, and record the user's confirmation. Must Not Do: do not set `report_persisted`.
- **extract_lessons**: Inputs = `artifacts/run_digest.json`, written by the hook the moment `assessment` passed (read it; never rebuild it in place). Output = `extraction_declaration.json` {schema, no_new_lessons, no_lessons_reason, covered_by, candidates, infra_noise, infra_evidence} plus 0 to 5 candidate directories under `artifacts/candidates/<nn>-<slug>/` (`proposal.json` + `proposal.md`) and, for facts, evidence files under `artifacts/memory_evidence/`. The full contract (digest fields, candidate formats, the gate's ten checks, the write-tools-only rule) is `sure/runtime/memory/EXTRACTION.md`; read it before writing anything. Write candidates and evidence first and the declaration last. `no_new_lessons: true` with a one-line reason is the normal result of a clean run. Must Not Do: do not run `scripts/build_run_digest.py` onto `artifacts/run_digest.json` (a preview goes to `--out <run_dir>/artifacts/run_digest.preview.json` and the gate ignores it); do not write under `sure/memory/` or `references/memory/`; do not use bash heredocs for these files. Failure: `scripts/check_memory_extraction.py` says which check failed; after two consecutive failures the hook advances on its own with `extraction: failed`, and switching to `no_new_lessons: true` with the reason is always a valid way out.
- **run_report**: {report_persisted, execution_path_actual}. Point `run_dir` at the source bundle, record the requested and resolved pipelines, the dataset set, `evaluation_only: true`, and the batch id. `check_run_report.py --profile eval` accepts a completed run only when `artifacts/eval_run_report.json` reports `status: success`; a failed run needs the failed `eval_run_report.json` and a `next_action`.

## Deterministic Backend

```bash
"$HARNESS_PYTHON_BIN" ../sure_infer/scripts/run_eval.py \
  --model <model> \
  --datasets <dataset__version,...> \
  --protocol-id <standard_system|strict_core> \
  --metric <metric> ... | --pipeline-id <exact-pipeline-id> ... \
  [--source-run <run_id|abs_dir>] \
  [--device <cpu|cuda[:index]>] \
  --invocation-run-dir <sure-run-dir>   # cwd = this skill package dir
```

`--invocation-run-dir` is an internal harness path and must resolve below the repository `.sure/runs/` root. The backend writes scratch evidence below `<sure-run-dir>/scratch/` and copies the terminal report to `<sure-run-dir>/artifacts/eval_run_report.json`. `run_eval.py` and `resolve_prediction_source.py` are the only backend scripts the hooks allow; `generate_predictions_via_server.py`, `run_model_mcp_smoke.py`, `model_wrapper_mcp_server.py`, `infer_entrypoint.py`, `server.py` and MCP `tools/call` are refused outright.

## Required Invocation Artifacts

### `artifacts/runtime_binding.json`

`schema=sure.skill.runtime_binding.v1`; binds the common Harness Runtime and the locked Evaluation Runtime, proves the Evaluation Runtime was built against that Harness Runtime, and declares why Model Runtime is not required. Required for successful and non-successful finishes alike.

### `artifacts/prediction_source_resolved.json`

`schema=sure.reval.approved_prediction_source.v2`: `source_kind`, exact model name, approved model path, verdict path and model fingerprint; protocol ID; sorted canonical dataset set and its digest; `source_results_dir`, `source_protocol`, `source_report` (`null` for a local run) and `source_report_sha256` (for a local run the hash of the sorted `[dataset, txt_sha256, txt_samples]` triples); each prediction path, SHA256 and non-empty row count; `inference_allowed=false`.

### `artifacts/eval_run_report.json`

`schema=sure.eval.run_report.v1` and proves:

- `evaluation_only=true`, `old_evaluation_reused=false`;
- `source_identity` equals `prediction_source_resolved.json`;
- `validation_payload.is_valid=true`;
- `pipeline_ids` equals the pipelines that actually ran (metrics are resolved before the run);
- the protocol reuse policy is `reused_predictions_no_inference`;
- `staging_append`: `staging_result_dir` is the source bundle (local) or its mirror below `sure/results` (approved), `batch_id` is `sure_eval_<24-hex>`, the batch manifest covers every scratch evaluation file exactly once, every requested `record_id` exists in the aggregate report and points into the batch, and the aggregate report and snapshot hashes match the receipt.

## Append Semantics

Each evaluation is an immutable unit at `evaluation_runs/sure_eval_<24-hex-id>/` inside the bundle: the complete scratch evaluation tree plus `artifact_manifest.json` (validation, route plan, reuse provenance, protocol evidence, evaluation payload, raw evaluator runs, metrics, pipeline descriptions, sample reports, predictions, run manifests). Paths stored in structured artifacts are result-relative. After the batch is durable, the bundle's `report.jsonl` receives the new rows and `report_snapshot.md` is regenerated; a directory lock, temporary files, `fsync` and atomic renames protect concurrent writers. `protocol.yaml` and `predictions/` keep the inference identity and are never rewritten. For an approved source the current approved `report.jsonl` must remain an exact prefix and every other approved artifact an exact hash match. Re-running the same route is an idempotent no-op; the same identity with different content is a hard collision for operator review.

## Memory (advisory)

Earlier runs leave agent-written notes. `sure/memory/index.md` (repo root) is the merged index: confirmed and provisional entries, one bullet each with its triggers. Confirmed files live under `../sure_infer/references/memory/bad_cases/` and `sure/skills/_shared/memory/facts/`. Nothing in them is human-reviewed: verify against evidence before relying on one, and never copy a command from an entry into an artifact without running it.

- At `pre_start` the hook writes `artifacts/memory_context.json` with the facts that match this run (shape quoted in the `dataset_scope` contract line above; written even when empty); `dataset_scope` reads it.
- When a gate blocks, the repair text may end with a block whose first line is `Memory (advisory, agent-written, not human-reviewed; verify against evidence before relying):`, listing at most two entries from earlier runs. Read the entry file named there when it looks relevant, then fix the artifact.
- `../sure_infer/references/memory/ROUTING.md` says when to open the index and the bad-case files by hand (the routing file is shared with `/sure_infer`, so its path reads `references/memory/ROUTING.md` from that package).
- `extract_lessons` (unit 4) writes what this run learned; the contract is `sure/runtime/memory/EXTRACTION.md`. Publishing to `sure/memory/provisional/` happens in `post_finish` without you; moving entries into `references/` is a human step.

## Forbidden Actions

- Never write to the approved model or result roots from this skill.
- Never run `generate_predictions_via_server.py`, model smoke/server scripts, `infer_entrypoint.py`, or MCP `tools/call`.
- Never select a dataset subset or infer a missing version.
- Never change the inference protocol during evaluation.
- Never append before prediction validation, evaluation, report validation, and route checks all pass.

## Success Criteria

The `pre_finish` hook enforces: `artifacts/runtime_binding.json` is valid for `sure_eval`, `main_agent_run_report.json` exists, the terminal gate passes, and the state machine reached the terminal unit. On success call `sure_finish` with `status: "success"` and `manifest_path: ".sure/runs/<run_id>/manifest.json"`. If incomplete or blocked, finish with `status: "incomplete"` or `status: "failed"` and a repair summary; when `artifacts/eval_run_report.json` exists it must then carry the same status, an `error_code`, `evaluation_only=true`, `inference_executed=false`, `old_evaluation_reused=false`, `append_attempted=false` and the resolved `source_identity`.

A `failed` or `incomplete` finish must also carry `artifacts/extraction_declaration.json` (see `sure/runtime/memory/EXTRACTION.md`, section 10): `pre_finish` returns a repair asking for it up to twice, then lets the run finish and records `extraction: failed`.
