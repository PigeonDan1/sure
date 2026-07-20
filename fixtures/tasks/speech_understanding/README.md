# Speech Understanding Composite Fixture Index

Use this index only for multi-task speech understanding models. Atomic subtasks
are indexed in their own task directories so routing can load only the needed
fixture and metric context.

Copy selected files into:

```text
sure/models/<model>/fixture/
```

## Atomic Fixture Routes

| Subtask | Fixture index | Representative set |
|---------|---------------|--------------------|
| ASR | `fixtures/tasks/asr/README.md` | `fixtures/tasks/asr/qwen3_asr_smoke/` |
| S2TT | `fixtures/tasks/s2tt/README.md` | `fixtures/tasks/s2tt/kimi_audio_s2tt_smoke/` |
| SER | `fixtures/tasks/ser/README.md` | `fixtures/tasks/ser/kimi_audio_ser_smoke/` |
| SLU | `fixtures/tasks/slu/README.md` | `fixtures/tasks/slu/kimi_audio_slu_smoke/` |
| GR | `fixtures/tasks/gr/README.md` | `fixtures/tasks/gr/kimi_audio_gr_smoke/` |
| SD | `fixtures/tasks/sd/README.md` | `fixtures/tasks/sd/librispeech_2spk_smoke/` |
| SA-ASR | `fixtures/tasks/sa_asr/README.md` | `fixtures/tasks/sa_asr/librispeech_2spk_smoke/` |

For a model that supports multiple subtasks, copy only the selected subtask
fixtures into the model-local layout:

```text
sure/models/<model>/fixture/<subtask>/<dataset>/
```

## Validation Metrics

Task-formatted metric namespaces:

```text
src/sure_eval/evaluation/tasks/asr/
src/sure_eval/evaluation/tasks/s2tt/
src/sure_eval/evaluation/tasks/classification/
src/sure_eval/evaluation/tasks/slu/
src/sure_eval/evaluation/tasks/sd/
src/sure_eval/evaluation/tasks/sa_asr/
```

Use task routes for benchmark-compatible scoring and task-local validation
scripts for phase-1 smoke checks. SLU additionally uses
`nodes/normalization/prompt_norm` before `nodes/scoring/classify`.
SD and SA-ASR use `nodes/scoring/meeteval` and annotation-file inputs rather
than key-tab text.

Record task-specific expected fields in `model.spec.yaml.io_contract` and
`spec_validation.json`.

## Related Tool-Agent Memory

- `docs/agents/model_tool_agent/task_playbooks/SPEECH_UNDERSTANDING.md`
- `docs/agents/model_tool_agent/contracts/fixture_policy.md`
- `docs/agents/model_tool_agent/contracts/minimal_validation.md`
