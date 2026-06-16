# KWS Fixture Index

Use this index for keyword spotting and wake-word detection models. Copy selected
positive and negative samples into:

```text
src/sure_eval/models/<model>/fixture/kws/
```

## Shared Fixture Set

Use:

```text
fixtures/tasks/kws/wenwen_smoke/kws/
```

Source:

```text
src/sure_eval/models/daydream_factory__keyword-spot-fsmn-ctc-wenwen/fixture/kws/
```

## Included Source

| Source | Files | Notes |
|--------|-------|-------|
| `src/sure_eval/models/daydream_factory__keyword-spot-fsmn-ctc-wenwen/fixture/kws/` | `audio/*.wav`, `gt.jsonl` | Positive and negative wake-word samples. |

## Expected Model-Local Layout

```text
src/sure_eval/models/<model>/fixture/kws/
├── gt.jsonl
└── audio/
    ├── positive_*.wav
    └── negative_*.wav
```

`gt.jsonl` should include the audio path, expected label, keyword, and whether
the sample is positive or negative.

## Validation Metrics

Task-formatted namespace:

```text
src/sure_eval/evaluation/kws/
```

No shared KWS metric has been moved in this phase. Use existing task-local
validation scripts for phase-1 checks.

Validation should include at least one positive and one negative sample.

## Related Tool-Agent Memory

- `docs/agents/model_tool_agent/task_playbooks/KWS.md`
- `docs/agents/model_tool_agent/contracts/fixture_policy.md`
- `docs/agents/model_tool_agent/contracts/minimal_validation.md`
