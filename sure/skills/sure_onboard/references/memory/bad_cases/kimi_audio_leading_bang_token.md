# Kimi-Audio Leading Bang Token Bad Case

## Trigger

Use this memory when Kimi-Audio or a similar dual text/audio stream model
produces predictions with a stable leading `!` or `！`, for example:

```text
!在此次申办冬奥会的过程中
!C
```

Do not treat this as metric normalization first.

## Affected Step

`VALIDATE_INFER`, wrapper generation, and text detokenization for speech
understanding multitask models.

## Minimum Evidence

Collect token-level evidence before patching:

- raw generated text token ids;
- per-token decode for the first several tokens;
- decoded text before wrapper or metric cleanup;
- tokenizer ids for Kimi special tokens such as `kimia_text_blank` and
  `kimia_text_eos`.

Known Kimi-Audio evidence from `moonshotai__Kimi-Audio-7B-Instruct` re-onboarding:

```json
{
  "input_tokens": [0, 18493, 101974],
  "per_token_decode": [
    {"id": 0, "text": "!"},
    {"id": 18493, "text": "在"},
    {"id": 101974, "text": "此次"}
  ],
  "special_tokens": {
    "kimia_text_blank": 151666,
    "kimia_text_eos": 151667
  }
}
```

This proves the leading `!` is token id `0`, a normal tokenizer token emitted
at the start of the generated text stream. It is not
`<|im_kimia_text_blank|>` and cannot be fixed by `skip_special_tokens`.

## Fix Pattern

Fix at the generation/detokenization boundary, not in metric calculation.

For Kimi-Audio, filter a leading token id `0` before text decode only when it
appears as a stream-boundary artifact followed by real content:

```python
if len(valid_text_ids) > 1 and valid_text_ids[0] == 0:
    valid_text_ids = valid_text_ids[1:]
```

Do not hide the problem by stripping `!` from final strings in
`clean_generated_text()` or inside metric code. That loses evidence and can
remove legitimate punctuation for other models.

## Verification

Run a small GPU validation with token debug enabled. The debug artifact should
show:

- `input_tokens` still starts with `0`;
- `filtered_text_ids` removes the leading `0`;
- `decoded` and saved `prediction` no longer start with `!`;
- metric report is computed from the corrected prediction files.

Known verification artifacts:

```text
src/sure_eval/models_reonboard/runs/asr_kimi_audio/artifacts/kimi_token_debug_after_fix.jsonl
src/sure_eval/models_reonboard/runs/asr_kimi_audio/artifacts/multitask_sample_output.json
src/sure_eval/models_reonboard/runs/asr_kimi_audio/artifacts/validation_multitask.log
```

Full five-task verification after the fix:

```text
date: 2026-06-21
job_id: <job-id>
partition: site-gpu-data
status: Completed
duration: 1m23s
token_debug: src/sure_eval/models_reonboard/runs/asr_kimi_audio/artifacts/kimi_token_debug_full.jsonl
sample_output: src/sure_eval/models_reonboard/runs/asr_kimi_audio/artifacts/multitask_sample_output.json
metric_report: src/sure_eval/models_reonboard/runs/asr_kimi_audio/artifacts/speech_understanding_metric_report.json
```

The full run still shows raw `input_tokens` beginning with token id `0`, while
`filtered_text_ids` removes that leading token and saved predictions no longer
start with `!`.
