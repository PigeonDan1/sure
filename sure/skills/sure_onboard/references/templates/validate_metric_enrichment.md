# Validate Metric Enrichment Reference

This reference preserves the metric-enrichment experience from the original
`templates/validate.py` without replacing the harness runtime template.

The harness runtime `scripts/templates/validate.py` must keep its staged CLI
contract:

```bash
python validate.py --stage import|load|infer|contract|all
```

Metric enrichment is a post-smoke activity. It should reuse artifacts produced
by import/load/infer/contract validation and must not silently rerun model
inference just to repair metric reports.

## Original Experience To Preserve

The original model-tool validation template included these useful behaviors:

- discover subtask fixtures under `fixture/<task>/<subtask>/gt.jsonl`;
- resolve audio paths relative to each `gt.jsonl`;
- run model inference once and write structured sample outputs;
- write `ref_<subtask>.txt` and `hyp_<subtask>.txt` from the same fixture and
  predictions;
- call the route-backed SURE evaluation layer instead of ad hoc metric code;
- write metric artifacts under `artifacts/metric_reports/<subtask>/`;
- save both `report.json` and `pipeline_description.json`;
- record backend, task, metric, score, report path, and pipeline description
  path in artifacts.

## Harness Adaptation

For `/sure_onboard`, keep validation and metric enrichment separated:

1. `validate_infer` writes `sample_output.json` and stage result JSONs.
2. `validate_contract` proves the output satisfies `MODEL_INPUT.io_contract`.
3. Optional metric enrichment reads existing `sample_output.json`, fixture
   `gt.jsonl`, and any generated ref/hyp files.
4. Metric enrichment writes task-specific reports such as:
   - `asr_metric_report.json`
   - `kws_metric_report.json`
   - `speech_understanding_metric_report.json`
   - `tts_metric_report.json`
   - `vc_metric_report_local_pipeline.json`
5. `verdict.json` may reference metric reports, but deployment success remains
   based on import/load/infer/contract and container package readiness, not on
   benchmark score.

## Required Metric Evidence

Metric reports should include:

- input files and sample ids;
- metric backend or task route;
- pipeline id and pipeline trace when available;
- conversion trace for SD/SA-ASR or other annotation conversion tasks;
- provider blockers when a metric dependency or checkpoint is unavailable;
- whether the report was produced from existing inference outputs or from a
  new inference run.

If the only change is metric semantics, do not rerun model inference. Reuse the
existing `sample_output.json` and model-local outputs.

## Metric Bypass

Hand-written edit distance (ASR), ad hoc MOS/SIM or file-size-only metrics
(TTS), ad hoc similarity without a provider report (VC) and a
speech-understanding report without its pipeline trace are all metric bypass:
the report is not accepted, whatever the numbers say.
