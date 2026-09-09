# SER Fixture Index

Approved harness fixture: `fixtures/tasks/ser/crema_d_smoke/`, containing
redistributable CREMA-D samples with source URLs and hashes in `provenance.json`.
The older fixture below remains available for existing integrations.

Representative fixture:

```text
fixtures/tasks/ser/kimi_audio_ser_smoke/iemocap/
```

Source:

```text
src/sure_eval/models/asr_kimi_audio/fixture/ser/iemocap/
```

Files:

- `*.wav`
- `gt.jsonl`

Use `src/sure_eval/evaluation/tasks/classification/` with
`nodes/scoring/classify` for accuracy metric setup.
