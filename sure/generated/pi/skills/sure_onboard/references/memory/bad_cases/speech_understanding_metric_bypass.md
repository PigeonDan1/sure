# Bad Case: Speech Understanding Metric Bypass

## Trigger

Read this when a speech-understanding or multitask audio model has ASR/S2TT/SER/SLU/GR
outputs, but final artifacts do not contain a metric report generated through
the task-scoped SURE evaluation modules.

Common symptoms:

- `validate_multitask.py` writes ref/hyp files but no combined metric report.
- ASR CER/WER is computed by local edit-distance helper code.
- S2TT BLEU/chrF is missing because `sacrebleu` is not installed.
- SER/GR accuracy is hand-written without recording
  `sure_eval.evaluation.tasks.classification.pipeline.evaluate_classification_files`.
- SLU accuracy is hand-written without recording the `normalization/prompt_norm`
  then `scoring/classify` pipeline trace.
- A later GPU failure overwrites `multitask_sample_output.json`, but usable
  `ref_*.txt` and `hyp_*.txt` artifacts still exist.

## Affected Step

`SAVE_ARTIFACTS` metric enrichment after multitask inference.

## Required Evidence

Collect:

- `ref_asr.txt` / `hyp_asr.txt`
- `ref_s2tt.txt` / `hyp_s2tt.txt`
- `ref_ser.txt` / `hyp_ser.txt`
- `ref_slu.txt` / `hyp_slu.txt`
- `ref_gr.txt` / `hyp_gr.txt`
- exact metric runner command
- output report path and `ok` / `errors` fields

## Fix

Do not rerun a large model just to refresh metrics. If ref/hyp artifacts already
exist, run:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
PYTHONPATH=src .venv.hostbak/bin/python scripts/run_speech_understanding_metric_pipeline.py \
  --model <model-name> \
  --artifacts-dir <model-dir>/artifacts \
  --tasks ASR,S2TT,SER,SLU,GR \
  --asr-language zh \
  --s2tt-language zh \
  --output <model-dir>/artifacts/speech_understanding_metric_report.json
```

The runner must use:

- `sure_eval.evaluation.tasks.asr.metrics.CERMetric` or `WERMetric`
- `sure_eval.evaluation.tasks.s2tt.metrics.BLEUMetric`
- SER/GR: `sure_eval.evaluation.tasks.classification.pipeline.evaluate_classification_files`
- SLU: `sure_eval.evaluation.tasks.slu.pipeline.evaluate_slu_files`

If S2TT fails with `No module named 'sacrebleu'`, install it into the active
SURE uv environment with清华源:

```bash
env UV_CACHE_DIR=/tmp/uv-cache uv pip install \
  -p .venv.hostbak/bin/python \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  sacrebleu
```

## Verification

```bash
.venv.hostbak/bin/python -m json.tool <model-dir>/artifacts/speech_understanding_metric_report.json
```

The report must have `ok: true`, `errors: []`, complete ASR/S2TT/SER/SLU/GR
task entries, and backend strings from the official evaluation modules above.
For SLU, `pipeline_trace` must include `normalization/prompt_norm` followed by
`scoring/classify`.

## Example

`src/sure_eval/models/moonshotai__Kimi-Audio-7B-Instruct`:

- report: `artifacts/speech_understanding_metric_report.json`
- ASR: CER 0.0
- S2TT: BLEU 36.726526249712926, chrF 26.50461077394935
- SER: accuracy 1.0
- SLU: accuracy 0.6666666666666666
- GR: accuracy 1.0
