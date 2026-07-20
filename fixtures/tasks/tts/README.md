# TTS Fixture Index

Use this index for text-to-speech models. Copy selected prompts and references
into:

```text
sure/models/<model>/fixture/tts/
```

## Shared Fixture Set

Use:

```text
fixtures/tasks/tts/indextts2_zh_smoke/zh/
```

Source:

```text
src/sure_eval/models/IndexTeam__IndexTTS-2/fixture/zh/
```

## Other Existing Sources

| Source | Language | Files | Notes |
|--------|----------|-------|-------|
| `src/sure_eval/models/SWivid__F5-TTS_Emilia-ZH-EN/fixture/zh/` | zh | `*.mp3`, `gt.jsonl` | Chinese TTS smoke references. |

## Expected Model-Local Layout

```text
sure/models/<model>/fixture/tts/
└── zh/
    ├── gt.jsonl
    └── reference_*.wav|mp3
```

`gt.jsonl` should describe text prompts, reference audio when needed, language,
and any speaker or style fields required by the model.

## Validation Metrics

Task-formatted namespace:

```text
src/sure_eval/evaluation/tasks/tts/
```

The task route and runner-compatible wrapper live under `tasks/tts`; reusable
transcription, speaker, and MOS providers live under `nodes/`.

For cloning-style TTS, validation must prove the model generated new audio; it
must not pass by returning reference audio directly.

## Related Tool-Agent Memory

- `docs/agents/model_tool_agent/task_playbooks/TTS.md`
- `docs/agents/model_tool_agent/contracts/fixture_policy.md`
- `docs/agents/model_tool_agent/contracts/minimal_validation.md`
