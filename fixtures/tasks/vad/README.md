# VAD Fixture Index

Use this fixture for standalone voice activity detection models. It is not an
ASR transcript fixture and not a speaker diarization fixture.

## Shared Fixture Set

```text
fixtures/tasks/vad/librispeech_vad_smoke/
├── gt.jsonl
├── librispeech_vad_001.expected.json
├── librispeech_vad_001.wav
└── provenance.json
```

The WAV is a deterministic derivative of LibriSpeech test-other utterance
`367-130732-0006`: 0.5 seconds of digital silence, the complete 2.35-second
utterance, then another 0.5 seconds of silence. Reference boundaries are the
complement of `ffmpeg` `silencedetect=noise=-40dB:d=0.2` on the derived WAV,
recorded on its 3.35-second timebase in `provenance.json`.

License: CC-BY-4.0. Attribution and hashes are recorded in `provenance.json`.

## Contracts

Model input:

```json
{"audio_path": "/absolute/path/to/librispeech_vad_001.wav"}
```

Minimum model output:

```json
{"speech_segments": [{"start": 0.551687, "end": 0.780875}, {"start": 1.033062, "end": 2.553813}]}
```

The protocol can represent an all-silence input with an empty
`speech_segments` array. This speech-bearing onboarding smoke deliberately
requires a non-empty result and does not exercise that separate profile.
`frame_scores` is optional. When present, each row uses seconds and has
`start`, `end`, and `score`. VAD output must not use speaker-labelled
`segments`; that belongs to SD/SA-ASR.

This fixture is currently wired only through Feed, Onboard, and Trans. The
`/sure_eval` prediction and routing bridge does not yet preserve structured VAD
rows, so this fixture must not be presented as end-to-end evaluation-ready.
