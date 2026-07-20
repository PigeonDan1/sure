# Main Agent ASSESSMENT_UNIT Contract

## Purpose

`ASSESSMENT_UNIT` is responsible for interpreting script outputs and deciding the final state of the current run.

## Required Output

- `status`
- `evidence`
- `blocking_issues`
- `next_action`

## Allowed Status Values

- `success`
- `partial_success`
- `prediction_complete_evaluation_incomplete`
- `blocked`
- `needs_tool_workflow`
- `needs_human_input`

For TTS/VC runs, `prediction_complete_evaluation_incomplete` is required when
prediction generation and validation passed but evaluation artifacts are missing
or the evaluation surface failed. This is not a model failure by itself. The
next action must be an evaluation-only retry using
[`tts_vc_audio_evaluation_surface.md`](tts_vc_audio_evaluation_surface.md).

## Must Not Do

- must not silently retry without explanation
- must not hide missing evidence
- for TTS/VC, must not treat a Completed inference vc job as success unless
  `evaluation_payload.json`, `report.jsonl`, `protocol.yaml`, and metric
  artifacts exist
- for TTS/VC, must not classify missing `ffmpeg` or missing metric runtime
  dependencies in the inference image as a model bad case

## Output Template

- [main_agent_assessment_report.json](../templates/main_agent_assessment_report.json)
