# KWS Fixture Index

Approved harness fixture: `fixtures/tasks/kws/librispeech_keyword_smoke/`, with
positive and negative examples derived from traceable LibriSpeech transcripts.
The older wake-word fixture below remains available.

Use this index for keyword spotting and wake-word detection models. Copy selected
positive and negative samples into:

```text
sure/models/<model>/fixture/kws/
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
sure/models/<model>/fixture/kws/
├── gt.jsonl
└── audio/
    ├── positive_*.wav
    └── negative_*.wav
```

`gt.jsonl` should include the audio path, expected label, keyword, and whether
the sample is positive or negative.

## Validation Metrics

Formal `/sure_eval` scoring uses the pinned evaluation engine's canonical KWS
routes:

```text
kws.any.accuracy.conversion_kws_sure_json_to_samples_v1.wekws_det_v1
kws.any.macro_recall.conversion_kws_sure_json_to_samples_v1.wekws_det_v1
```

The report includes accuracy, precision, recall, F1, macro recall, false reject
rate, false alarm rate, false alarms per hour, and a DET threshold curve. The
current SURE JSON route evaluates each threshold as
`detected AND score >= threshold`; it does not recover candidates that a model
has already suppressed internally. A production DET claim therefore requires
the model wrapper to expose threshold-independent candidate scores.

An inference dataset source must declare `task: KWS` in its `sample.jsonl` or
`ds.jsonl`. Each row must carry `key` or `sample_id`, an audio path, `keywords`,
an explicit positive/negative label (`expected`, `label`, or
`expected_detected`), and a positive duration in seconds. Positive rows also
need an unambiguous `expected_keyword`. The source projector accepts the
fixture-style `audio` field and the pool-style `attribute.path`; it writes the
canonical `kws_wakeword_v1` projection consumed by `/sure_infer` and
`/sure_eval`.

Validation should include at least one positive and one negative sample.

## Related Tool-Agent Memory

- `docs/agents/model_tool_agent/task_playbooks/KWS.md`
- `docs/agents/model_tool_agent/contracts/fixture_policy.md`
- `docs/agents/model_tool_agent/contracts/minimal_validation.md`
