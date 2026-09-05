# CLI Predict Contract

## Scope

This contract defines the first unified `sure-eval predict` surface.

The first supported execution path is:

- task: `asr`
- model: `asr_qwen3`
- execution mode: direct Python wrapper invocation

## Inputs

`sure-eval predict` consumes JSONL records shaped like `sure.inference_input.v1`.

Minimum required fields:

- `instance_id`
- `task`
- `input.audio_path`
- `input.sample_rate`

## Outputs

The command writes:

- one unified prediction JSONL file shaped like `sure.prediction.v1`
- one manifest JSON file next to the output JSONL

`predict` is responsible for inference only. It does not score predictions and
does not call the existing evaluation scripts.

## Error Handling

- one bad item must not silently abort the batch
- each failed item must still emit a prediction row
- failed rows must use `status="error"` and include a structured `error` object

## Non-Goals In V1

- multi-task support beyond ASR
- dataset-specific export logic
- long-lived server orchestration
- metric computation
