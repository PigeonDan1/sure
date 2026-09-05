# ASR Metric Bypass

## Trigger

Read this bad case when any ASR onboarding or re-onboarding artifact contains
WER/CER, but the implementation uses local helpers such as:

- `def _edit_distance(...)`
- `def edit_distance(...)`
- custom punctuation / whitespace normalization for WER/CER
- metric fields without a backend such as
  `sure_eval.evaluation.tasks.asr.metrics.CERMetric`

## Affected Step

`VALIDATE_CONTRACT` / `SAVE_ARTIFACTS`.

The model inference may be correct, but the reported ASR metric is not
benchmark-compatible if it bypasses the SURE ASR metric path.

## Required Evidence

Collect:

- `sample_output.json` containing `reference` / `ground_truth` and prediction text.
- The validation script or post-processing script that produced metric fields.
- Import check for the official metric path:

```bash
PYTHONPATH=src .venv.hostbak/bin/python -c \
  "from sure_eval.evaluation.tasks.asr.metrics import CERMetric, WERMetric; print(CERMetric, WERMetric)"
```

## Required Fix

Do not rerun model inference just to fix metric semantics.

Instead:

1. Read existing `sample_output.json`.
2. Extract each sample's prediction and reference.
3. Recompute Chinese ASR with `CERMetric().calculate_batch(..., language="zh")`.
4. Recompute English ASR with `WERMetric().calculate_batch(..., language="en")`.
5. Replace only metric fields and related verdict/summary metric values.
6. Record metric backend:

```json
{
  "backend": "sure_eval.evaluation.tasks.asr.metrics.CERMetric"
}
```

## Verification

Run a smoke import and at least one real artifact recomputation:

```bash
PYTHONPATH=src .venv.hostbak/bin/python -c \
  "from sure_eval.evaluation.tasks.asr.metrics import CERMetric; print(CERMetric().calculate('你好世','你好世界', language='zh').score)"
```

Then inspect the changed `sample_output.json` and confirm every ASR metric has
`backend` under `details` or the metric object.

## Affected Examples

- `src/sure_eval/models_reonboard/runs/asr_fireredasr`
- `src/sure_eval/models_reonboard/runs/asr_parakeet`
- `src/sure_eval/models_reonboard/runs/asr_qwen3`
- `src/sure_eval/models_reonboard/runs/asr_sensevoice_small`
- `src/sure_eval/models_reonboard/runs/whisper_large_v3_turbo`
- `src/sure_eval/models_reonboard/runs/GilgameshWind__X-ASR-zh-en`
