# Voice Conversion Fixture Index

Use this index for voice conversion models. Copy selected source/reference
samples into:

```text
sure/models/<model>/fixture/vc/
```

## Shared Fixture Set

Use:

```text
fixtures/tasks/vc/seed_vc_zh_smoke/zh/
```

Source:

```text
src/sure_eval/models/Plachtaa__seed-vc/fixture/zh/
```

The source fixture currently contains audio files only. During onboarding, create
the model-local `gt.jsonl` required by the target wrapper contract.

## Included Source

| Source | Language | Files | Notes |
|--------|----------|-------|-------|
| `src/sure_eval/models/Plachtaa__seed-vc/fixture/zh/` | zh | `*.mp3` | Chinese voice conversion smoke samples. |

## Expected Model-Local Layout

```text
sure/models/<model>/fixture/vc/
└── zh/
    ├── gt.jsonl
    ├── source_*.wav|mp3
    └── target_*.wav|mp3
```

If an existing source directory has audio but no `gt.jsonl`, create the
model-local `gt.jsonl` during onboarding and record the source paths in
`spec_validation.json`.

## Validation Metrics

Task-formatted namespace:

```text
src/sure_eval/evaluation/tasks/vc/
```

The task route and runner-compatible wrapper live under `tasks/vc`; reusable
semantic, speaker, and MOS providers are shared with TTS nodes.

Validation should check that output audio exists, is decodable, has non-zero
duration, and is not just a copied input file.

## Related Tool-Agent Memory

- `docs/agents/model_tool_agent/task_playbooks/VC.md`
- `docs/agents/model_tool_agent/contracts/fixture_policy.md`
- `docs/agents/model_tool_agent/contracts/minimal_validation.md`
