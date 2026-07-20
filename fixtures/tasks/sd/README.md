# SD Fixture Index

SD fixtures validate speaker diarization model outputs during deployment smoke
tests. The model should return speaker time segments for the provided audio.

## Shared Fixture Set

Use:

```text
fixtures/tasks/sd/librispeech_2spk_smoke/
```

Source: LibriSpeech `test-clean`, using short utterances from two different
speakers per smoke sample.

Files:

- `gt.jsonl`
- `librispeech_2spk_001.wav`
- `librispeech_2spk_002.wav`
- `librispeech_2spk_003.wav`

`gt.jsonl` is the primary deployment-validation input. Each row contains one
two-speaker recording, an audio path, and speaker time segments:

```json
{"key":"librispeech_2spk_001","audio":"librispeech_2spk_001.wav","segments":[{"speaker":"spk1","start":0.0,"end":3.275}]}
```

Speaker labels are local to each row and normalized to `spk1` and `spk2`.
