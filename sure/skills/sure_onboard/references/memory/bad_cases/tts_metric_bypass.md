# TTS Metric Bypass

## Trigger

Read this bad case when a TTS onboarding/re-onboarding run:

- only checks that a generated wav exists;
- checks sample rate, duration, file size, or "not prompt copy" but reports that
  as the final TTS evaluation;
- writes MOS/SIM/semantic fields from local helper logic;
- lacks `tts_metric_report.json`; or
- does not reference `sure_eval.evaluation.tasks.tts`.

## Affected Step

`VALIDATE_CONTRACT` / `SAVE_ARTIFACTS`.

Audio contract validation proves the wrapper produced a decodable file. It does
not prove the TTS evaluation path is connected to SURE-EVAL.

## Required Evidence

Collect:

- Existing `sample_output.json` with generated audio, prompt/reference audio,
  target text, and language.
- Generated audio path under `artifacts/outputs/` or `docker_artifacts/outputs/`.
- Import check for the official TTS metric namespace:

```bash
PYTHONPATH=src <python> -c \
  "from sure_eval.evaluation.tasks.tts import TTSSample, TTSMetricPipeline, build_default_tts_metric_pipeline; print(TTSSample, TTSMetricPipeline, build_default_tts_metric_pipeline)"
```

## Required Fix

Do not rerun TTS model inference just to fix metric semantics.

Instead:

1. Read existing `sample_output.json`.
2. Build a `TTSSample` using:
   - `prediction_audio`: generated wav
   - `reference_text`: target synthesis text
   - `reference_audio`: prompt/reference audio
   - `language`: fixture/model language
3. Evaluate with `sure_eval.evaluation.tasks.tts`.
4. Write `tts_metric_report.json`.
5. If a provider is missing a checkpoint, dependency, or GPU resource, record a
   structured blocker in the report. Do not replace it with hand-written metrics.
6. Update `verdict.json` with `tts_evaluation.backend = "sure_eval.evaluation.tasks.tts"`.

Preferred existing runner:

```bash
cd /hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
.venv.hostbak/bin/python scripts/run_tts_metric_pipeline_docker.py \
  --prediction-audio <generated.wav> \
  --reference-audio <prompt_or_reference_audio> \
  --reference-text '<target synthesis text>' \
  --language zh \
  --sample-id <sample_id> \
  --gpu 0 \
  --device cuda:0 \
  --work-dir <run_dir>/docker_artifacts/tts_metric_parts \
  --output <run_dir>/docker_artifacts/tts_metric_report_local_pipeline.json
```

Do not try to solve missing metric dependencies by installing large evaluator
stacks into the model Docker first. The established TTS metric runner already
splits semantic, speaker, and MOS providers across known-good evaluator images
and still calls `src/sure_eval/evaluation/tasks/tts` internally.

## Verification

At minimum:

```bash
PYTHONPATH=src <python> -m sure_eval.evaluation.tasks.tts.validate_metrics --suite pipeline --device cpu
```

For model artifacts, inspect `tts_metric_report.json` and confirm it references
`sure_eval.evaluation.tasks.tts` and the real generated audio file.

## Affected Examples

- `src/sure_eval/models_reonboard/runs/SWivid__F5-TTS_Emilia-ZH-EN`
- `src/sure_eval/models_reonboard/runs/IndexTeam__IndexTTS-2`

Known successful report:

```text
src/sure_eval/models_reonboard/runs/IndexTeam__IndexTTS-2/docker_artifacts/tts_metric_report_local_pipeline.json
```
