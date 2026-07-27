# /sure_reval

Reuse existing SURE prediction files and recompute evaluation routes without running model inference.

This is a separate atomic capability from `/sure_eval`. `/sure_eval` owns model inference plus evaluation. `/sure_reval` owns the case where predictions already exist and the user wants to change metrics, normalization, route choices, or exact `pipeline_id` selections.

## Parameters

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `source` | yes | Existing results dir, canonical eval run dir, or bare `predictions/` dir. |
| `model` | no | Model name override. Inferred from source when possible. |
| `datasets` | no | Comma-separated dataset list. Inferred from source when omitted. |
| `metrics` | no | Metric list for current default route selection, for example `cer` or `wer`. |
| `pipeline_id` | no | Exact standalone sure-evaluation pipeline id. Repeat or comma-separate to compare multiple pipelines for the same metric. |
| `max_samples` | no | Bounded validation/evaluation sample count. Existing predictions are filtered to the first N dataset samples. |
| `output_dir` | no | Fresh re-evaluation run directory. Defaults under `~/tmp/sure_reval/`. |
| `tmp_root` | no | Tmp root used when `output_dir` is omitted. |
| `device` | no | Evaluation device. Default `cpu`; audio metrics may need `cuda`. |
| `copy_mode` | no | `copy` or `hardlink`. Default `copy`; filtered runs always write new files. |

## Source Types

`source` can point at any of these shapes:

| Source kind | Required files | Notes |
|-------------|----------------|-------|
| `results_dir` | `predictions/`, usually `report.jsonl` and `protocol.yaml` | Preferred when reusing a published or mirrored evaluation result. Model, protocol, and datasets are inferred when possible. |
| `run_dir` | `predictions/` plus run-local metadata | Preferred when continuing from a canonical model-local evaluation run. |
| `predictions_dir` | `<dataset>.txt` and optionally `<dataset>.jsonl` | Use `model=...` and `datasets=...` when source metadata cannot be inferred. |

## Metric Selection

Use `metrics` when the user wants the current default route for a reported
metric. Use `pipeline_id` when the user wants an exact route variant such as a
specific normalizer, transcriber, scorer, or speaker/MOS provider.

Do not pass both `metrics` and `pipeline_id` for the same user intent. Prefer
`pipeline_id` for comparability experiments because it makes the selected route
identity explicit in `evaluation_payload.json`, `pipeline_description.json`, and
`report.json`.

## Rules

- Do not start model servers.
- Do not call model inference scripts.
- Do not copy old `evaluation_payload.json`, `report.jsonl`, `protocol.yaml`, `metrics/`, or `sample_reports/` from the source run.
- Reuse only prediction files, then validate and recompute metrics through the current `sure/external/sure-evaluation` engine.
- Prefer `pipeline_id` when the user wants to compare route or normalizer changes for the same metric.
- Keep verification outputs in tmp unless the user explicitly asks for a compatibility `results/` mirror.

## Backend

Use the shared deterministic backend under `../sure_eval/scripts/`:

```bash
python3 ../sure_eval/scripts/run_reval.py \
  --source <source> \
  --datasets <dataset> \
  --max-samples 5 \
  --pipeline-id <pipeline-id-1> \
  --pipeline-id <pipeline-id-2>
```

The backend writes a fresh tmp run with:

```text
prediction_source_resolved.json
prediction_reuse_manifest.json
validation_payload.json
evaluation_payload.json
evaluation_route_plan.json
protocol.yaml
report.jsonl
metrics/
sample_reports/
report_snapshot.md
model_eval_manifest.json
main_agent_run_report.json
reval_run_report.json
```

Important generated fields:

| Artifact | Field | Expected value |
|----------|-------|----------------|
| `prediction_source_resolved.json` | `source_kind` | `results_dir`, `run_dir`, or `predictions_dir`. |
| `prediction_reuse_manifest.json` | `source.old_evaluation_reused` | Always `false`. |
| `prediction_reuse_manifest.json` | `imported[].copy_mode` | `copy`, `hardlink`, or `filtered_copy`. Bounded runs usually use `filtered_copy`. |
| `prediction_reuse_manifest.json` | `imported[].imported_samples` | Number of predictions copied into the fresh reval run. Must match `max_samples` when a positive cap is requested and enough source rows exist. |
| `evaluation_route_plan.json` | `engine.commit` | Commit of the standalone `sure-evaluation` engine used for this reval run. |
| `evaluation_payload.json` | `results[].pipeline_id` | Exact pipeline executed for that dataset-metric result. |
| `evaluation_payload.json` | `results[].artifacts.metric_artifact_dir` | Fresh metric artifact directory. Duplicate metric names are disambiguated with `metric__pipeline_id`. |
| `reval_run_report.json` | `evaluation_only` | Always `true`. |
| `reval_run_report.json` | `old_evaluation_reused` | Always `false`. |
| `reval_run_report.json` | `summary.comparisons[].pipelines[]` | Per-pipeline `pipeline_id`, ordered `nodes`, and `score`. |
| `reval_run_report.json` | `summary.comparisons[].score_spread` | Max score minus min score for that dataset/metric comparison group. |

After the backend completes, copy or write the generated `reval_run_report.json`
into the Sure invocation artifact path:

```text
<sure-run-dir>/artifacts/reval_run_report.json
```

The `pre_finish` hook rejects completion until `artifacts/reval_run_report.json`
exists and points to a valid tmp re-evaluation run.

## Completion Checklist

Before finishing `/sure_reval`, verify:

- `reval_run_report.json` exists and passes `scripts/check_reval_run_report.py`.
- `main_agent_run_report.json` notes that no model server was started and no inference was run.
- `reval_run_report.json.evaluation_only` is `true`.
- `reval_run_report.json.old_evaluation_reused` is `false`.
- `prediction_reuse_manifest.json.imported[].imported_samples` matches the requested bounded sample scope.
- `validation_payload.json.is_valid` is `true`.
- For exact pipeline runs, `evaluation_payload.results[].pipeline_id`,
  `metrics/<dataset>/<metric_slug>/pipeline_description.json.pipeline_id`, and
  `metrics/<dataset>/<metric_slug>/report.json.pipeline_id` are equal.
- `evaluation_payload.results[].nodes` matches the node order in
  `report.json.pipeline_trace`.
- Multiple pipelines for the same dataset and metric have separate
  `metric_artifact_dir` paths and separate sample reports.
