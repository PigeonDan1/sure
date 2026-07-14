# Task Playbook Routing

This file controls which task-specific memory the model tool-agent should read.
Do not load every task playbook by default.

## Inputs

Route from the normalized task fields in `MODEL_INPUT` and `model.spec.yaml`:

- `task_type`
- `supported_tasks`
- `allowed_tasks`
- model README or upstream claim, only when the structured fields are missing

Use uppercase task names in routing decisions: `ASR`, `SPEECH_UNDERSTANDING`,
`TTS`, `VC`, `KWS`.

## Default Rule

Always read:

- `docs/agents/model_tool_agent/AGENTS.md`
- this routing file

Then read only the task playbook selected below.

If the task cannot be determined, stop and classify the task first. Do not
fallback to reading all task playbooks.

## Route Table

| Task signal | Read | Do not read by default |
|-------------|------|------------------------|
| `ASR`, `asr`, automatic speech recognition, speech-to-text only | `task_playbooks/ASR.md` | `TTS.md`, `VC.md`, `KWS.md`, `SPEECH_UNDERSTANDING.md` |
| `S2TT`, `SER`, `SLU`, `GR`, or multi-task speech understanding | `task_playbooks/SPEECH_UNDERSTANDING.md` | `TTS.md`, `VC.md`, `KWS.md` unless the model also supports those tasks |
| `TTS`, text-to-speech, speech synthesis | `task_playbooks/TTS.md` | `ASR.md`, `VC.md`, `KWS.md` |
| `VC`, voice conversion, timbre conversion, speech conversion | `task_playbooks/VC.md` | `ASR.md`, `TTS.md`, `KWS.md` |
| `KWS`, keyword spotting, wake word detection | `task_playbooks/KWS.md` | `ASR.md`, `TTS.md`, `VC.md` |

## Multi-Task Models

Read one playbook per supported task. Keep the selected set explicit.

Example:

```yaml
supported_tasks: [ASR, S2TT, SER, SLU, GR]
read:
  - task_playbooks/SPEECH_UNDERSTANDING.md
  - task_playbooks/ASR.md
```

For multi-task speech understanding models, `SPEECH_UNDERSTANDING.md` is the
composite playbook. Add `ASR.md` when ASR is part of the validation plan. For
S2TT/SER/SLU/GR, use the atomic fixture indexes listed by the composite
playbook.

## Fixture Linkage

Each selected task playbook must point to the task fixture index once the shared
fixture library is available. Until then, model-local fixture directories remain
valid:

```text
sure/models/<model>/fixture/<task>/
```

## Required Audit Record

Record the selected task playbooks in one of:

- `artifacts/build_plan.json`
- `artifacts/spec_validation.json`
- `artifacts/tool_agent_run_report.json`

Use this shape:

```json
{
  "context_selection": {
    "task_playbooks_read": [
      "docs/agents/model_tool_agent/task_playbooks/ASR.md"
    ],
    "task_playbooks_skipped": [
      "docs/agents/model_tool_agent/task_playbooks/TTS.md"
    ],
    "reason": "MODEL_INPUT.task_type is ASR"
  }
}
```
