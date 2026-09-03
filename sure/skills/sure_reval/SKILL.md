# /sure_reval

Recompute one or more exact evaluation pipelines from an approved NFS prediction set. This skill is evaluation-only: it must not start a model server, invoke an MCP model tool, generate predictions, or reuse old evaluation scores.

## Trust Boundary

Approved inputs are read only from:

```text
<approved_models_root>/<model>
<approved_results_root>/<model>/<approved-result>/
```

The runtime must never write to either NFS root. Permanent output is a complete append-only result mirror below:

```text
sure/results/<same-relative-path-as-nfs-result>/
```

The first reval copies the complete approved NFS result as its baseline. Each new evaluation is preserved as `evaluation_runs/<deterministic-batch-id>/`, while root `report.jsonl` and `report_snapshot.md` are aggregate views. An operator reviews this local result mirror and promotes it into NFS manually. Invocation evidence and evaluator scratch files also remain under `.sure/runs/<run_id>/`.

At pre-start, `artifacts/runtime_binding.json` records the exact materialized Harness Runtime and Evaluation Runtime, including IDs, lock hashes, executables, manifests, engine commit, and their cross-binding. Model Runtime is explicitly `required=false`: Reval consumes approved predictions and running any model environment is a contract violation. The declaration is required for both successful and incomplete terminal reports.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `model` | yes | Exact approved NFS model directory name. No aliases or paths. |
| `datasets` | yes | Complete comma-separated approved set. Every item must be `<dataset_name>__<version_id>`. |
| `pipeline_id` | yes | One or more exact sure-evaluation pipeline IDs. |
| `protocol` | no | `standard_system` (default) or `strict_core`; must equal the approved result protocol. |
| `device` | no | Evaluation device. This never changes prediction identity. |
| `output_dir` | no | Absolute directory where the harness collects this invocation's `result.json` and control artifacts. The harness consumes it before the agent starts, so re-evaluation products keep their approved mirror layout. The directory must be outside every configured `forbidden_output_roots` entry and writable. |

The removed parameters `source`, `reuse_predictions_from`, `model_dir`, `tmp_root`, `copy_mode`, `max_samples`, `metrics`, `config`, and `evaluation_engine_root` are forbidden. They weaken source identity, replace the approved evaluator, or turn a full re-evaluation into a bounded test.

Example:

```text
/sure_reval model=Qwen__Qwen3-ASR-1.7B datasets=aishell1__v1.0.2 pipeline_id=asr.zh.cer.identity_norm_v1.wenet_cer_v1
```

## State Machine

1. `resolve_approved_model`: resolve the exact model below the NFS model root and require `config.yaml` plus a successful verdict. Model deployment runtime files are not read because re-evaluation never runs inference.
2. `resolve_approved_result`: inspect only immediate approved result directories for that model.
3. `verify_source_identity`: require exact model, protocol, and sorted dataset-set equality; bind the NFS report, model fingerprint, prediction hashes, and sample counts.
4. `resolve_evaluation_route`: resolve every requested exact `pipeline_id` through the standalone sure-evaluation engine.
5. `run_evaluation_in_scratch`: copy approved predictions into invocation scratch, validate them, and run current evaluation nodes. No inference surface is allowed.
6. `append_local_result_bundle`: after every validation/report gate passes, persist the complete evaluation artifact tree in a deterministic batch, append route rows to root `report.jsonl`, and refresh root `report_snapshot.md`.
7. `finish`: validate the terminal report, source identity, protocol reuse evidence, reference-data hash, evaluator commit/source hash, route identity, NFS baseline mirror, artifact manifest, report paths, and appended record IDs.

The source gate rejects aliases, unversioned datasets, subsets, supersets, protocol drift, model drift, missing predictions, symlink escapes, malformed reports, and ambiguous approved results.
It also requires the approved NFS result or model bundle to contain
`references/sure_benchmark/jsonl`. `/sure_reval` never reads a harness-local,
dataset-platform, sandbox, or environment-overridden reference root. Legacy
approved results without the reference projection stop with
`INPUT_EVIDENCE_MISSING` and must be repaired and promoted by an operator
before re-evaluation.

## Deterministic Backend

The agent runs:

```bash
"$HARNESS_PYTHON_BIN" ../sure_infer/scripts/run_reval.py \
  --model <model> \
  --datasets <dataset__version> \
  --protocol-id <standard_system|strict_core> \
  --pipeline-id <exact-pipeline-id> \
  --invocation-run-dir <sure-run-dir>
```

`--invocation-run-dir` is an internal harness path and must resolve below the repository `.sure/runs/` root. The backend writes scratch evidence below `<sure-run-dir>/scratch/` and copies the terminal report to `<sure-run-dir>/artifacts/reval_run_report.json`.

## Required Invocation Artifacts

### `artifacts/runtime_binding.json`

Must use `schema=sure.skill.runtime_binding.v1`, bind the common Harness Runtime and locked Evaluation Runtime, prove the Evaluation Runtime was built against that Harness Runtime, and declare why Model Runtime is not required.

### `artifacts/prediction_source_resolved.json`

Must use `schema=sure.reval.approved_prediction_source.v2` and include:

- exact model name, NFS model path, verdict path, and model fingerprint;
- exact protocol ID;
- sorted canonical dataset set and its digest;
- NFS result/report/protocol paths and base report SHA256;
- each prediction path, SHA256, and non-empty row count;
- `source_kind=approved_nfs_results`;
- `inference_allowed=false`.

### `artifacts/reval_run_report.json`

Must use `schema=sure.reval.run_report.v1` and prove:

- `evaluation_only=true`;
- `old_evaluation_reused=false`;
- source identity equals `prediction_source_resolved.json`;
- `validation_payload.is_valid=true`;
- requested exact pipelines occur in the evaluation payload;
- protocol reuse policy is `reused_predictions_no_inference`;
- the local staging result is based on the current approved NFS result and report hash;
- every scratch evaluation artifact is covered by the persisted batch manifest;
- every requested deterministic `record_id` exists in the staging report and points into that batch;
- the aggregate snapshot and report hashes match the append receipt.

## Append Semantics

The first reval for an approved result mirrors its complete NFS directory into the matching `sure/results` path. Later runs require the current NFS `report.jsonl` as an exact prefix and every other immutable NFS artifact as an exact hash match. Root `protocol.yaml` and `predictions/` retain the approved inference identity; reval does not overwrite them.

Each evaluation is an immutable unit at:

```text
evaluation_runs/sure_reval_<24-hex-id>/
```

It contains the complete scratch evaluation tree plus `artifact_manifest.json`, including validation, route plan, reuse provenance, protocol evidence, evaluation payload, raw evaluator runs, metrics, pipeline descriptions, sample reports, predictions, and run manifests. Paths stored in structured artifacts are result-relative so the result directory can be promoted without retaining scratch or `sure/results` absolute paths.

After the batch is durable, root `report.jsonl` receives the new rows and root `report_snapshot.md` is regenerated from the aggregate report. A directory lock, temporary directories/files, `fsync`, atomic directory rename, and atomic file replace protect concurrent writers. The report is the final commit point.

Each appended row retains the normal dataset-metric report fields and adds `reval` metadata containing:

- deterministic `record_id`;
- model fingerprint;
- protocol ID;
- canonical dataset ID;
- prediction SHA256;
- reference dataset JSONL path, SHA256, and non-empty row count;
- exact pipeline and ordered nodes;
- evaluation engine commit and evaluation-source tree SHA256 (covers uncommitted custom routes);
- approved base report hash;
- `inference_executed=false` and `old_evaluation_reused=false`.

Re-running the same route is an idempotent no-op. The same identity with different content is a hard collision and requires operator review.

## Forbidden Actions

- Never write to `nfs/models` or `nfs/results` from this skill.
- Never accept a user-provided filesystem source.
- Never run `generate_predictions_via_server.py`, model smoke/server scripts, or MCP `tools/call`.
- Never select a dataset subset or infer a missing version.
- Never change the inference protocol during re-evaluation.
- Never append before prediction validation, evaluation, report validation, and route checks all pass.
