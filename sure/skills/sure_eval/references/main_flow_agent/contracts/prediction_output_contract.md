# Prediction Output Contract

## Purpose

This contract defines the model prediction files consumed by the main-flow
evaluation scripts.

It is intentionally task-facing: model inference code and main-flow agents
should use this file to decide how a tool response is normalized before
validation and evaluation.

This contract belongs to the main-flow evaluation surface. It does not define
or modify the model tool-agent workflow.

## Files

For each concrete dataset split, prediction generation should write:

```text
predictions/<dataset>.txt
predictions/<dataset>.jsonl
```

The TSV file is the compatibility view:

```text
key<TAB>normalized_prediction
```

The JSONL file is the preferred structured view. Each row must include:

```json
{"key":"<key>","dataset":"<dataset>","task":"<task>","language":"<language>","prediction":{},"normalized_prediction":"<value>","raw_response":{}}
```

`normalized_prediction` must be exactly the value written to the second TSV
column.

## Task Row Templates

One valid structured prediction row for each task:

```json
{"key":"<key>","dataset":"<dataset>","task":"ASR","language":"<lang>","prediction":{"text":"<transcript>"},"normalized_prediction":"<transcript>","raw_response":{}}
{"key":"<key>","dataset":"<dataset>","task":"S2TT","language":"<lang>","prediction":{"text":"<translation>"},"normalized_prediction":"<translation>","raw_response":{}}
{"key":"<key>","dataset":"<dataset>","task":"TTS","language":"<lang>","prediction":{"audio_path":"<generated_audio_path>","sample_rate":24000,"duration_ms":0},"normalized_prediction":"<generated_audio_path>","raw_response":{}}
{"key":"<key>","dataset":"<dataset>","task":"VC","language":"<lang>","prediction":{"audio_path":"<converted_audio_path>","sample_rate":24000,"duration_ms":0},"normalized_prediction":"<converted_audio_path>","raw_response":{}}
{"key":"<key>","dataset":"<dataset>","task":"SER","language":"<lang>","prediction":{"label":"<emotion_label>"},"normalized_prediction":"<emotion_label>","raw_response":{}}
{"key":"<key>","dataset":"<dataset>","task":"GR","language":"<lang>","prediction":{"label":"<gender_label>"},"normalized_prediction":"<gender_label>","raw_response":{}}
{"key":"<key>","dataset":"<dataset>","task":"SLU","language":"<lang>","prediction":{"text":"<answer_or_option>","label":"<optional_label>"},"normalized_prediction":"<answer_or_option>","raw_response":{}}
{"key":"<key>","dataset":"<dataset>","task":"SD","language":"<lang>","prediction":{"segments":[{"speaker":"S1","start":0.0,"end":1.0}]},"normalized_prediction":"[{\"speaker\":\"S1\",\"start\":0.0,\"end\":1.0}]","raw_response":{}}
{"key":"<key>","dataset":"<dataset>","task":"SA-ASR","language":"<lang>","prediction":{"segments":[{"speaker":"S1","start":0.0,"end":1.0,"text":"<words>"}]},"normalized_prediction":"[{\"speaker\":\"S1\",\"start\":0.0,\"end\":1.0,\"text\":\"<words>\"}]","raw_response":{}}
{"key":"<key>","dataset":"<dataset>","task":"KWS","language":"<lang>","prediction":{"keyword":"<keyword>","score":0.0,"events":[]},"normalized_prediction":"0.0","raw_response":{}}
```

## TSV Projection Rules

The TSV compatibility file must use these second-column values:

| Task | TSV second column |
| --- | --- |
| ASR | transcript text |
| S2TT | translated text |
| TTS | generated audio path |
| VC | converted audio path |
| SER | label |
| GR | label |
| SLU | answer text or label |
| SD | annotation JSON or annotation file path |
| SA-ASR | annotation JSON or annotation file path |
| KWS | score, event JSON, or task-specific score path |

## Validation Requirements

All tasks:

- every expected dataset key appears once;
- no extra keys are present;
- `normalized_prediction` is non-empty when non-empty output is required;
- TSV and JSONL normalized values agree when both files are present.

Task-specific checks:

- ASR/S2TT/SLU require text or label-like output;
- SER/GR require `prediction.label`;
- TTS/VC require `prediction.audio_path` and the audio file must exist;
- SD/SA-ASR require either `prediction.segments` or an annotation file path;
- KWS requires score/event output or a task-specific score file.
