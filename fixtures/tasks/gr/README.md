# GR Fixture Index

Approved harness fixture: `fixtures/tasks/gr/librispeech_gender_smoke/`, with
labels derived from LibriSpeech speaker metadata and recorded in
`provenance.json`. The older fixture below remains available.

Representative fixture:

```text
fixtures/tasks/gr/kimi_audio_gr_smoke/librispeech-test-clean/
```

Source:

```text
src/sure_eval/models/asr_kimi_audio/fixture/gr/librispeech-test-clean/
```

Files:

- `*.wav`
- `gt.jsonl`

Use `src/sure_eval/evaluation/tasks/classification/` with
`nodes/scoring/classify` for accuracy metric setup.
