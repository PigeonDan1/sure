# Speech Understanding Composite Fixture Index

`speech_understanding` is an engine-bound suite, not a task guessed from model
keywords. It always expands to every public task route discovered from the
pinned `sure-evaluation` engine. A task added to or removed from the engine must
be reflected here through the generated capability check before CI passes.

Copy selected files into:

```text
sure/models/<model>/fixture/
```

## Atomic Fixture Routes

| Subtask | Fixture index | Representative set |
|---------|---------------|--------------------|
| ASR | `fixtures/tasks/asr/README.md` | `fixtures/tasks/asr/qwen3_asr_smoke/` |
| Classification | `fixtures/tasks/classification/README.md` | `fixtures/tasks/classification/librispeech_speaker_smoke/` |
| GR | `fixtures/tasks/gr/README.md` | `fixtures/tasks/gr/librispeech_gender_smoke/` |
| KWS | `fixtures/tasks/kws/README.md` | `fixtures/tasks/kws/librispeech_keyword_smoke/` |
| S2TT | `fixtures/tasks/s2tt/README.md` | `fixtures/tasks/s2tt/kimi_audio_s2tt_smoke/` |
| SA-ASR | `fixtures/tasks/sa_asr/README.md` | `fixtures/tasks/sa_asr/librispeech_2spk_smoke/` |
| SD | `fixtures/tasks/sd/README.md` | `fixtures/tasks/sd/librispeech_2spk_smoke/` |
| SE | `fixtures/tasks/se/README.md` | `fixtures/tasks/se/librispeech_noise_smoke/` |
| SER | `fixtures/tasks/ser/README.md` | `fixtures/tasks/ser/crema_d_smoke/` |
| SLU | `fixtures/tasks/slu/README.md` | `fixtures/tasks/slu/fluent_speech_commands_smoke/` |
| SV | `fixtures/tasks/sv/README.md` | `fixtures/tasks/sv/librispeech_trials_smoke/` |
| TSE | `fixtures/tasks/tse/README.md` | `fixtures/tasks/tse/librispeech_mix_smoke/` |
| TTS | `fixtures/tasks/tts/README.md` | `fixtures/tasks/tts/librispeech_prompt_smoke/` |
| VAD | `fixtures/tasks/vad/README.md` | `fixtures/tasks/vad/librispeech_silence_smoke/` |
| VC | `fixtures/tasks/vc/README.md` | `fixtures/tasks/vc/librispeech_voice_conversion_smoke/` |

For this suite, stage every listed atomic fixture into the model-local layout:

```text
sure/models/<model>/fixture/<subtask>/<dataset>/
```

## Validation Metrics

Task-formatted metric namespaces:

```text
src/sure_eval/evaluation/tasks/asr/
src/sure_eval/evaluation/tasks/s2tt/
src/sure_eval/evaluation/tasks/classification/
src/sure_eval/evaluation/tasks/kws/
src/sure_eval/evaluation/tasks/slu/
src/sure_eval/evaluation/tasks/sd/
src/sure_eval/evaluation/tasks/sa_asr/
src/sure_eval/evaluation/tasks/se/
src/sure_eval/evaluation/tasks/sv/
src/sure_eval/evaluation/tasks/tse/
src/sure_eval/evaluation/tasks/tts/
src/sure_eval/evaluation/tasks/vad/
src/sure_eval/evaluation/tasks/vc/
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
