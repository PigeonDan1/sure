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

Arabic source and redistribution terms:

```text
Dataset: google/fleurs
Revision: 70bb2e84b976b7e960aa89f1c648e09c59f894dd
Configuration/split: ar_eg/test
Sentence IDs: 1993, 1730, 1995
License: CC-BY-4.0
Provenance: qwen3_asr_smoke/asr-ar/provenance.json
```

## Other Existing Sources

| Source | Language | Files | Notes |
|--------|----------|-------|-------|
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
