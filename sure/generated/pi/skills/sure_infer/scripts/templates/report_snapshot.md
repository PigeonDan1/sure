# {model_full_name} Evaluation Snapshot

## Basic Information

| Field | Value |
|---|---|
| Model name | `{model_full_name}` |
| Model source | {model_source} |
| Task | {task} |
| Dataset scope | `{datasets}` |
| Run ID | `{run_id}` |
| Output directory | `{run_dir}` |
| Standard results mirror | `{results_dir}` |
| Report JSONL | `{standard_report_jsonl}` |
| Evaluation payload | `{evaluation_payload_path}` |
| Validation payload | `{validation_payload_path}` |
| Status | {status} |

## Formatting Policy

- Numeric display values in human-facing tables should use two decimal places unless the field is an identifier, version, path, timestamp, hash, or raw JSON-derived string.
- Percent scores should be rendered with a `%` suffix, for example `1.58%`.
- Raw fraction scores should be rendered as decimals, for example `0.02`.
- Missing optional values should be rendered as `N/A`.
- Boolean values should be rendered as `True` or `False`.
- Paths, dataset names, model names, run IDs, pipeline IDs, node IDs, and metric names should be wrapped in backticks.
- Tables should preserve one row per dataset-metric unless the section explicitly describes nodes or artifacts.

## Evaluation Scope

| Field | Value |
|---|---|
| Result granularity | dataset_metric |
| Report row count | {dataset_metric_row_count} |
| Dataset count | {dataset_count} |
| Metric count | {metric_count} |
| Sample-level result layout | `sample_reports/<dataset>/<metric_slug>.jsonl` |
| Metric artifact layout | `metrics/<dataset>/<metric_slug>/` |
| Report row rule | one row per dataset metric |

## Dataset Scope

Expected format:

| Dataset | Task | Lang | Metrics | Samples | JSONL |
|---|---:|---:|---|---:|---|

{dataset_scope}

## Result Summary

| Artifact | Path |
|---|---|
| Run-local result file | `{run_report_jsonl}` |
| Standard result mirror | `{standard_report_jsonl}` |

Expected format:

| Dataset | Task | Lang | Samples | Metric | Score | Status |
|---|---:|---:|---:|---:|---:|---|

{result_summary}

## Result Field Notes

| Field | Meaning | Format |
|---|---|---|
| `Dataset` | Evaluated dataset identifier | Backticked string |
| `Task` | Dataset task family | Short uppercase task name |
| `Lang` | Dataset language used for route selection | ISO-like short language code |
| `Samples` | Number of evaluated samples | Two-decimal number |
| `Metric` | Metric selected for the dataset | Uppercase display name |
| `Score` | Display score for the selected metric | Two-decimal percent for error-rate metrics |
| `Status` | Dataset-metric evaluation status | `success`, `failed`, or `skipped` |

Notes:

- ASR error-rate metrics such as WER and CER are lower-is-better.
- `Score` should be derived from `metric.display` when available.
- Keep this summary table to the listed columns only.

## Per-Dataset Test Results

The standard mirror report contains {dataset_metric_row_count} dataset-metric rows.

Expected format:

| Dataset | Metric | Display Score | Raw Score | Unit | Higher Is Better | Prediction Validation |
|---|---:|---:|---:|---|---:|---|

{per_dataset_test_results}

## Metric Details

Expected format:

| Dataset | Metric | Score Key | Display Score | Raw Score | Unit | Higher Is Better | Status |
|---|---:|---|---:|---:|---|---:|---|

{metric_details}

## Validation Summary

Expected format:

| Dataset | Expected | Provided | Missing | Extra | Duplicate | Empty | Valid |
|---|---:|---:|---:|---:|---:|---:|---|

{validation_summary}

## Validation Details

| Field | Value |
|---|---|
| Prediction contract | `docs/agents/main_flow_agent/contracts/prediction_output_contract.md` |
| Compatibility prediction file | `predictions/<dataset>.txt` |
| Structured prediction file | `predictions/<dataset>.jsonl` |
| Required non-empty predictions | {require_nonempty_predictions} |

Validation checks:

- Expected sample count
- Provided prediction count
- Missing keys
- Extra keys
- Duplicate keys
- Empty predictions
- Structured missing keys
- Structured extra keys
- Structured duplicate keys
- Invalid structured rows
- Contract violation keys

Expected detail format:

| Dataset | Format | Prediction TXT | Prediction JSONL | Structured Valid | Contract Violations | Notes |
|---|---|---|---|---:|---:|---|

{validation_details}

## Evaluation Pipeline

Expected format:

| Dataset | Metric | Pipeline ID | Stage | Node | Node Version | Manifest |
|---|---:|---|---|---|---|---|

{evaluation_pipeline}

## Pipeline Trace Details

| Field | Value |
|---|---|
| Evaluation entrypoint | `scripts/evaluate_predictions.py` |
| Metric router | `sure_eval.evaluation.scripts.run_task(...)` |
| Route source | `src/sure_eval/evaluation/tasks/<task>/routes.yaml` |
| Metric report layout | `metrics/<dataset>/<metric_slug>/report.json` |
| Pipeline description layout | `metrics/<dataset>/<metric_slug>/pipeline_description.json` |
| Conversion trace policy | preserved when `conversion_steps` or `conversion_trace` exists |

Expected detail format:

| Dataset | Metric | Pipeline ID | Report | Description | Conversion Steps | Node Count |
|---|---:|---|---|---|---:|---:|

{pipeline_trace_details}

## Evaluation Runtime And Tool Versions

Expected format:

| Component | Version / Path |
|---|---|

{evaluation_runtime_versions}

## Failed Or Skipped Evaluations

Expected format:

| Dataset | Metric | Status | Reason | Artifact |
|---|---:|---|---|---|

{failed_or_skipped_evaluations}

## Output Artifacts

Expected format:

| Artifact | Dataset | Metric | Path |
|---|---|---:|---|

{output_artifacts}

## Artifact Groups

| Group | Contents |
|---|---|
| Report artifacts | `report.jsonl`, `evaluation_payload.json`, `report_snapshot.md` |
| Validation artifacts | `validation_payload.json`, prediction validation summaries |
| Prediction artifacts | Standard mirror predictions under `results/<model>/<protocol>/predictions/` |
| Metric artifacts | Route-backed per-dataset metric reports under `metrics/<dataset>/<metric_slug>/` |
| Sample reports | Per-sample metric reports under `sample_reports/<dataset>/<metric_slug>.jsonl` |

## Test Notes

Expected format:

- One concise note per bullet.
- Include the generator script for evaluation artifacts.
- Include whether the results directory is a mirror or the source of truth.
- Include known limitations or missing optional data.

{test_notes}
