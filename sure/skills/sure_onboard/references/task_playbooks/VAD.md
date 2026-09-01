# VAD Onboarding

Use this playbook only for standalone voice activity detection models.

## Classification

- VAD accepts audio and returns speech/non-speech timing. It does not return a
  transcript and does not assign speaker identities.
- A system that runs VAD only as a frontend and then emits text remains ASR.
- Speaker-labelled intervals are SD or SA-ASR, not VAD.
- Record `task_type: vad`; never hide a VAD model under `asr` or `sd` to reuse a
  fixture.

## Model Contract

Canonical MCP tool: `vad_predict`.

Input:

```json
{"audio_path": "/absolute/path/to/audio.wav"}
```

Minimum output:

```json
{"speech_segments": [{"start": 0.5, "end": 2.85}]}
```

`start` and `end` are finite seconds with `0 <= start < end`. Segments must be
sorted and non-overlapping. `frame_scores` is optional and is needed only for
the AUC route; do not synthesize scores from hard segments.

Use this `io_contract`:

```yaml
input_type: audio_path
output_type: json
primary_field: speech_segments
required_fields: [speech_segments]
nonempty_fields: [speech_segments]
json_serializable: true
```

This onboarding profile uses a speech-bearing smoke fixture, so its acceptance
contract requires a non-empty `speech_segments` result. That is a smoke-gate
choice, not a claim that the VAD protocol cannot represent an all-silence input
with `speech_segments: []`; such coverage needs a separately reviewed fixture
profile.

## Fixture

Use `fixtures/tasks/vad/README.md`. Reference rows require `key`, positive
seconds `duration`, and `speech_segments`. The model-local fixture remains
bounded to at most five samples.

## Evaluation Handoff

This playbook stops at the Feed, Onboard, and Trans handoff. The current
`/sure_eval` prediction and routing bridge does not preserve structured VAD
rows, so a VAD bundle must not be described as end-to-end evaluation-ready.
Connecting that bridge requires a separate reviewed change; do not rewrite or
bypass the locked evaluation engine from this playbook.
