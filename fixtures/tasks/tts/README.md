# TTS Fixture Index

Use this index for text-to-speech models. Copy selected prompts and references
into:

```text
src/sure_eval/models/<model>/fixture/tts/
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
src/sure_eval/models/<model>/fixture/tts/
└── zh/
    ├── gt.jsonl
    └── reference_*.wav|mp3
```

`gt.jsonl` should describe text prompts, reference audio when needed, language,
and any speaker or style fields required by the model.

## Validation Metrics

Task-formatted namespace:

```text
src/sure_eval/evaluation/tts/
```

No shared TTS metric has been moved in this phase. Use existing task-local
validation scripts for phase-1 checks.

For cloning-style TTS, validation must prove the model generated new audio; it
must not pass by returning reference audio directly.

## Related Tool-Agent Memory

- `docs/agents/model_tool_agent/task_playbooks/TTS.md`
- `docs/agents/model_tool_agent/contracts/fixture_policy.md`
- `docs/agents/model_tool_agent/contracts/minimal_validation.md`
