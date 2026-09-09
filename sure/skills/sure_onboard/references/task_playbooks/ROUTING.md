# Task Playbook Routing

This file controls which task-specific memory the model tool-agent should read.
Do not load every task playbook by default.

## Inputs

Route from the normalized task fields in `MODEL_INPUT` and `model.spec.yaml`:

- `task_type`
- `supported_tasks`
- `allowed_tasks`
- model README or upstream claim, only when the structured fields are missing

Normalize task names through the generated evaluation capability registry before
routing. The registry, rather than this document, defines the accepted set.

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
| `Classification`, `S2TT`, `SER`, `SLU`, `GR`, `SD`, `SA-ASR`, `SE`, `SV`, `TSE`, or `VAD` | `task_playbooks/SPEECH_UNDERSTANDING.md` | Other atomic playbooks unless the model also supports those tasks |
| `SPEECH_UNDERSTANDING` | `task_playbooks/SPEECH_UNDERSTANDING.md` plus every atomic playbook present for generated suite members | None of the generated suite members |
| `TTS`, text-to-speech, speech synthesis | `task_playbooks/TTS.md` | `ASR.md`, `VC.md`, `KWS.md` |
| `VC`, voice conversion, timbre conversion, speech conversion | `task_playbooks/VC.md` | `ASR.md`, `TTS.md`, `KWS.md` |
| `KWS`, keyword spotting, wake word detection | `task_playbooks/KWS.md` | `ASR.md`, `TTS.md`, `VC.md` |

## Multi-Task Models

Read one playbook per supported atomic task. Keep the selected set explicit.
`speech_understanding` is the exception to selective routing: it expands to all
public tasks in `engine-capabilities.generated.json`.

Example:

```yaml
supported_tasks: [ASR, S2TT, SER, SLU, GR]
read:
  - task_playbooks/SPEECH_UNDERSTANDING.md
  - task_playbooks/ASR.md
```

For `speech_understanding`, the composite playbook and fixture index enumerate
the full engine-bound suite. Do not infer or remove suite members from model
README keywords.

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
