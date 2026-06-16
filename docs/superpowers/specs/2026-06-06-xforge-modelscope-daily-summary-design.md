# XForge ModelScope Daily Summary Design

Date: 2026-06-06
Status: Draft approved for implementation planning

## Goal

Build the first version of a semi-automatic XForge-to-SURE ModelScope workflow.
The system runs daily on AISpeech/HPC, discovers task-relevant ModelScope
models and datasets, writes local summaries for human review, and downloads
only the resources selected by a human operator.

This version intentionally does not auto-download the daily recommendations.
It produces auditable local reports and command examples so a human can decide
which models or datasets are worth fetching and evaluating.

## Scope

Covered tasks:

- `asr`
- `s2tt`
- `slu`
- `gr`
- `ser`

Covered resources:

- ModelScope models
- ModelScope datasets

Daily recommendations:

- Recommend Top 3 models per task.
- Recommend Top 3 datasets per task.
- Top 3 means recommended in the local summary, not automatically downloaded.

## Non-Goals

- Do not implement full automatic download and evaluation in this version.
- Do not infer arbitrary dataset schemas.
- Do not automatically convert a dataset to SURE JSONL without an explicit
  schema mapping or a known adapter.
- Do not replace existing SURE model onboarding or evaluation contracts.
- Do not edit XForge skills or SURE agent flow files as part of the bridge.

## User Flow

Daily discovery runs from cron or a long-running AISpeech/HPC job:

```bash
python scripts/xforge_daily_modelscope_summary.py \
  --tasks asr s2tt slu gr ser \
  --top-k 3 \
  --date today \
  --output-root reports/xforge/modelscope
```

The script writes a local Markdown report and machine-readable JSON artifacts:

```text
reports/xforge/modelscope/YYYY-MM-DD/summary.md
reports/xforge/modelscope/YYYY-MM-DD/summary.json
reports/xforge/modelscope/YYYY-MM-DD/candidates.json
```

A human reads `summary.md`, chooses resources, and runs commands copied from
the report.

Model fetch:

```bash
python scripts/xforge_modelscope_fetch.py \
  --resource model \
  --task asr \
  --id <modelscope_model_id>
```

Dataset fetch:

```bash
python scripts/xforge_modelscope_fetch.py \
  --resource dataset \
  --task asr \
  --id <modelscope_dataset_id>
```

Dataset fetch with explicit mapping:

```bash
python scripts/xforge_modelscope_fetch.py \
  --resource dataset \
  --task asr \
  --id <modelscope_dataset_id> \
  --schema-mapping <mapping.yaml>
```

## Components

### Daily Summary Script

`scripts/xforge_daily_modelscope_summary.py` coordinates discovery and report
generation.

Responsibilities:

- Query ModelScope for each configured task and resource type.
- Normalize heterogeneous ModelScope API responses into bridge candidates.
- Score and rank candidates.
- Write Markdown and JSON summaries.
- Continue running other tasks if one task query fails.

It should reuse `xforge_sure_bridge.modelscope_watcher` primitives where useful,
but it should not perform downloads.

### Fetch Script

`scripts/xforge_modelscope_fetch.py` performs human-selected downloads.

Responsibilities:

- Accept explicit `--resource`, `--task`, and `--id`.
- Create an XForge bridge manifest for the selected resource.
- For models, call the existing model collection/materialization path so SURE
  receives model-local artifacts.
- For datasets, collect raw data and generate handoff artifacts. Conversion to
  SURE JSONL happens only when a schema mapping or known adapter is available.
- Write success or failure summaries for audit.

### Bridge Library

`xforge_sure_bridge` remains the shared implementation layer.

Expected additions:

- Task configuration for `asr`, `s2tt`, `slu`, `gr`, `ser`.
- Candidate scoring and ranking helpers.
- Markdown summary rendering helpers.
- Provider-specific fetch helpers where current bridge support is incomplete.

Existing bridge behavior should remain compatible with current tests.

## Candidate Ranking

Ranking is applied independently per task and resource type.

Primary signals:

1. Candidate updated on the report date.
2. Higher ModelScope download count.
3. Stronger task match from tags, task field, pipeline field, name, and summary.
4. Recency as a secondary tie-breaker.

The summary should clearly distinguish:

- Recommended Top 3 models.
- Other model candidates.
- Recommended Top 3 datasets.
- Other dataset candidates.

If a download count is missing, treat it as `0` and keep the candidate eligible.
If update time is missing or unparsable, include the candidate in "other"
candidates unless task matching is strong enough to surface it.

## File Layout

Daily report artifacts:

```text
reports/xforge/modelscope/YYYY-MM-DD/
├── summary.md
├── summary.json
└── candidates.json
```

Fetch artifacts:

```text
data/artifacts/xforge/modelscope/manifests/*.json
data/artifacts/xforge/modelscope/handoff/*.handoff.json
data/artifacts/xforge/modelscope/fetch_runs/*.json
```

Model artifacts:

```text
src/sure_eval/models/<model_name>/
├── checkpoints/
├── .runtime/
└── artifacts/
    ├── weights_manifest.json
    └── xforge_collect_summary.json
```

Dataset artifacts:

```text
data/datasets/xforge_raw/<dataset_name>/
data/datasets/xforge_sure/<dataset_name>.jsonl
```

`data/datasets/xforge_sure/<dataset_name>.jsonl` is created only when schema
mapping is available.

## SURE Handoff

Model fetch emits handoff for SURE tool onboarding:

```json
{
  "event_type": "xforge_model_discovered",
  "target_agent": "sure_tool_agent",
  "next_state": "FETCH_WEIGHTS",
  "status": "ready_for_model_collect"
}
```

After fetch succeeds, `weights_manifest.json` is the evidence that SURE can
continue from `FETCH_WEIGHTS`.

Dataset fetch emits handoff for SURE main flow:

```json
{
  "event_type": "xforge_dataset_discovered",
  "target_agent": "sure_main_agent",
  "next_state": "DATASET_SCOPE_UNIT",
  "status": "blocked_until_dataset_schema_mapping"
}
```

If a schema mapping is supplied and conversion succeeds, the status can advance
to a ready-for-dataset-preparation state with the generated SURE JSONL path.

## Error Handling

Daily summary failures:

- A failure in one task or resource type must not fail the whole daily run.
- The Markdown report must include a failure section with task, resource type,
  command context, and error message.
- The JSON summary must preserve structured error details.

Fetch failures:

- The fetch command exits non-zero.
- A failure summary is written under
  `data/artifacts/xforge/modelscope/fetch_runs/`.
- The summary records provider, resource type, task, id, command, error, and
  timestamp.

The implementation must not disable TLS verification to work around local CA
issues. The runtime environment should provide a valid CA bundle.

## AISpeech/HPC Operation

The daily summary script is intended to run from cron or a long-running job on
AISpeech/HPC.

Operational requirements:

- Use a stable Python environment with `modelscope` installed.
- Configure ModelScope cache locations under shared storage or another
  approved writable path.
- Keep report output under the repository or a configured artifact directory.
- Keep download outputs under model-local or dataset-local directories.
- Record exact commands in Markdown and JSON for reproducibility.

Example cron entry:

```cron
15 2 * * * cd /path/to/sure-eval-sandbox && /path/to/python scripts/xforge_daily_modelscope_summary.py --tasks asr s2tt slu gr ser --top-k 3 --date today --output-root reports/xforge/modelscope
```

## Testing Plan

Unit tests:

- Candidate extraction from varied ModelScope-like payloads.
- Task matching for all five tasks.
- Ranking with updated date, download count, and task match.
- Top 3 grouping for models and datasets.
- Markdown command rendering.
- JSON summary structure.
- Per-task failure isolation.
- Fetch model manifest and handoff generation.
- Dataset fetch without schema mapping remains blocked.

Integration-style tests using local fixtures:

- Offline daily summary from fixture candidates.
- Re-running summary with the same input produces stable output.
- Local-source model fetch produces `weights_manifest.json`.
- Dataset fixture with schema mapping converts to SURE JSONL.

Environment checks:

- `scripts/xforge_daily_modelscope_summary.py --help`
- `scripts/xforge_modelscope_fetch.py --help`
- Existing xforge bridge tests continue to pass.

## Open Implementation Notes

ModelScope's online API endpoint and parameters must be verified against the
current official API before implementation is considered complete. The current
repository has an older watcher default endpoint that may not match the latest
ModelScope OpenAPI behavior.

Remote ModelScope downloads require the `modelscope` Python package in the
runtime environment. The fetch script should report a clear error if the package
is missing.
