# ASR Fixture Index

Use this index for automatic speech recognition models. Copy selected files into:

```text
sure/models/<model>/fixture/asr/
```

## Shared Fixture Set

Use:

```text
fixtures/tasks/asr/qwen3_asr_smoke/
├── asr-ar/
├── asr_en/
└── asr_zh/
```

Chinese and English source:

```text
src/sure_eval/models/asr_qwen3/fixture/asr/
```

Arabic source:

```text
Dataset: aispeech_phy_gigaspeechbench_low_resource_languages_syr_test
Subset: raws/sample, Syrian Arabic (SYR)
Fixture records: SYR#0d54cea98e7d__seg_000018 through _000020
License: not declared in the source metadata; internal-use fixture pending owner confirmation
```

## Other Existing Sources

| Source | Language | Files | Notes |
|--------|----------|-------|-------|
| `src/sure_eval/models/GilgameshWind__X-ASR-zh-en/fixture/asr/asr_zh/` | zh | `sample_*.wav`, `gt.jsonl` | Same structure as Qwen3 ASR Chinese fixture. |
| `src/sure_eval/models/GilgameshWind__X-ASR-zh-en/fixture/asr/asr_en/` | en | `sample_*.wav`, `gt.jsonl` | Same structure as Qwen3 ASR English fixture. |
| `src/sure_eval/models/asr_parakeet/fixture/asr/asr_en/` | en | `sample_*.wav`, `gt.jsonl` | English ASR fixture. |

## Expected Model-Local Layout

```text
sure/models/<model>/fixture/asr/
├── asr-ar/
│   ├── gt.jsonl
│   └── sample_*.wav
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
src/sure_eval/evaluation/tasks/asr/
├── metrics.py
└── README.md

src/sure_eval/evaluation/nodes/scoring/wenet_wer/
└── wenet_compute_cer.py
```

Expected metric routing:

- Arabic ASR: CER
- Chinese ASR: CER
- English ASR: WER

`CERMetric` and `WERMetric` are class-name wrappers around the formal SURE ASR
scoring path, not the old simple edit-distance implementation.

## Related Tool-Agent Memory

- `docs/agents/model_tool_agent/task_playbooks/ASR.md`
- `docs/agents/model_tool_agent/contracts/fixture_policy.md`
- `docs/agents/model_tool_agent/contracts/minimal_validation.md`
