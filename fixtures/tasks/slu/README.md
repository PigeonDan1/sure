# SLU Fixture Index

Approved harness fixture: `fixtures/tasks/slu/fluent_speech_commands_smoke/`,
containing real Fluent Speech Commands test rows with source URLs and hashes in
`provenance.json`. The older fixture below remains available for existing
integrations.

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

Use `src/sure_eval/evaluation/tasks/slu/` for scoring. The route first runs
`nodes/normalization/prompt_norm` with `prompt_jsonl`, then runs
`nodes/scoring/classify` for accuracy.
