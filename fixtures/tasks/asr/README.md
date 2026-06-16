# ASR Fixture Index

Use this index for automatic speech recognition models. Copy selected files into:

```text
src/sure_eval/models/<model>/fixture/asr/
```

## Shared Fixture Set

Use:

```text
fixtures/tasks/asr/qwen3_asr_smoke/
├── asr_en/
└── asr_zh/
```

Source:

```text
src/sure_eval/models/asr_qwen3/fixture/asr/
```

## Other Existing Sources

| Source | Language | Files | Notes |
|--------|----------|-------|-------|
| `src/sure_eval/models/GilgameshWind__X-ASR-zh-en/fixture/asr/asr_zh/` | zh | `sample_*.wav`, `gt.jsonl` | Same structure as Qwen3 ASR Chinese fixture. |
| `src/sure_eval/models/GilgameshWind__X-ASR-zh-en/fixture/asr/asr_en/` | en | `sample_*.wav`, `gt.jsonl` | Same structure as Qwen3 ASR English fixture. |
| `src/sure_eval/models/asr_parakeet/fixture/asr/asr_en/` | en | `sample_*.wav`, `gt.jsonl` | English ASR fixture. |

## Expected Model-Local Layout

```text
src/sure_eval/models/<model>/fixture/asr/
├── asr_zh/
│   ├── gt.jsonl
│   └── sample_*.wav
└── asr_en/
    ├── gt.jsonl
    └── sample_*.wav
```

`gt.jsonl` should contain at least:

```json
{"key": "sample-id", "audio": "sample.wav", "ground_truth": "reference text", "language": "zh"}
```

## Validation Metrics

The task-formatted ASR evaluation files live under:

```text
src/sure_eval/evaluation/asr/
├── metrics.py
├── pyproject.toml
├── README.md
└── wenet_compute_cer.py
```

Expected metric routing:

- Chinese ASR: CER
- English ASR: WER

`CERMetric` and `WERMetric` are class-name wrappers around the formal SURE ASR
scoring path, not the old simple edit-distance implementation.

## Related Tool-Agent Memory

- `docs/agents/model_tool_agent/task_playbooks/ASR.md`
- `docs/agents/model_tool_agent/contracts/fixture_policy.md`
- `docs/agents/model_tool_agent/contracts/minimal_validation.md`
