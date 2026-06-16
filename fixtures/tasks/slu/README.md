# SLU Fixture Index

Representative fixture:

```text
fixtures/tasks/slu/kimi_audio_slu_smoke/mmsu/
```

Source:

```text
src/sure_eval/models/asr_kimi_audio/fixture/slu/mmsu/
```

Files:

- `sample_*.wav`
- `gt.jsonl`

Use `src/sure_eval/evaluation/classification/` for accuracy-style scoring, and
`SUREEvaluator.evaluate("SLU", ..., prompt_jsonl=...)` when prompt processing is
required.
