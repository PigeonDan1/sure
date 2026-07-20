# Bad Case: VC Metric Bypass

## Trigger

Read this when a VC model has a converted audio file and `VALIDATE_CONTRACT`
passed, but artifacts do not contain a metric report produced through
`src/sure_eval/evaluation/tasks/vc`.

Common symptoms:

- `verdict.json` only records `audio_path`, `sample_rate`, and `num_samples`.
- A local script computes ad hoc similarity or MOS fields without using
  `scripts/run_vc_metric_pipeline.py` or `scripts/run_vc_metric_pipeline_docker.py`.
- The model is rerun just to refresh metrics although converted audio already
  exists.
- `source_audio`, `reference_audio`, and `reference_text` are ambiguous.

## Affected Step

`VALIDATE_CONTRACT` -> `SAVE_ARTIFACTS` metric enrichment for VC models.

## Required Evidence

Collect:

- converted audio path;
- source audio path;
- reference audio path;
- source-text ground truth used as `reference_text`;
- exact metric wrapper command;
- output JSON path and `ok` / `errors` status.

## Fix

Do not rerun model inference for metric-only refresh. Run the shared VC metric
wrapper against the existing output audio:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
.venv.hostbak/bin/python scripts/run_vc_metric_pipeline_docker.py \
  --converted-audio <converted.wav> \
  --reference-audio <speaker_reference.wav> \
  --source-audio <source_content.wav> \
  --reference-text '<source text>' \
  --language zh \
  --sample-id <sample-id> \
  --gpu 0 \
  --device cuda:0 \
  --output <model>/artifacts/vc_metric_report_local_pipeline.json
```

If the TTS metric cache is already populated, VC may reuse it because the VC
pipeline reuses TTS semantic, speaker, and MOS providers. Record the chosen
`cache_dir` in `verdict.json` or the run report.

## Verification

```bash
.venv.hostbak/bin/python -m json.tool <model>/artifacts/vc_metric_report_local_pipeline.json
```

The report must have `ok: true`, `errors: []`, and metrics from
`sure_eval.evaluation.tasks.vc` such as `vc_cer`, `sim/*`, `dnsmos`, `wv-mos`, or
`utmos`.

## Example

`src/sure_eval/models/Plachta__Seed-VC`:

- converted audio: `artifacts/outputs/seed_vc_v2_smoke.wav`
- source audio: `fixture/zh/ZH_B00000_S00000_W000002.mp3`
- reference audio: `fixture/zh/ZH_B00001_S00000_W000000.mp3`
- report: `artifacts/vc_metric_report_local_pipeline.json`
