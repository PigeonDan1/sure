# Shared Task Fixture Index

This directory is the canonical library and index for reusable task fixtures.
Each task has one approved smoke fixture selected by
`sure/runtime/evaluation/harness-task-profiles.json`. Existing model-local and
legacy fixtures remain valid and are not deleted by this registry.

## Purpose

The model tool-agent should choose fixture candidates from this task-level index
and then copy the selected files into the model-local validation directory:

```text
sure/models/<model>/fixture/<task>/
```

This keeps validation reproducible while avoiding context-heavy searches across
all model directories.

## Task Index

| Task | Fixture index | Approved fixture |
|------|---------------|------------------|
| ASR | `asr/README.md` | `asr/qwen3_asr_smoke/asr_en/` |
| Classification | `classification/README.md` | `classification/librispeech_speaker_smoke/` |
| GR | `gr/README.md` | `gr/librispeech_gender_smoke/` |
| KWS | `kws/README.md` | `kws/librispeech_keyword_smoke/` |
| S2TT | `s2tt/README.md` | `s2tt/kimi_audio_s2tt_smoke/covost2-en2zh/` |
| SA-ASR | `sa_asr/README.md` | `sa_asr/librispeech_2spk_smoke/` |
| SD | `sd/README.md` | `sd/librispeech_2spk_smoke/` |
| SE | `se/README.md` | `se/librispeech_noise_smoke/` |
| SER | `ser/README.md` | `ser/crema_d_smoke/` |
| SLU | `slu/README.md` | `slu/fluent_speech_commands_smoke/` |
| SV | `sv/README.md` | `sv/librispeech_trials_smoke/` |
| TSE | `tse/README.md` | `tse/librispeech_mix_smoke/` |
| TTS | `tts/README.md` | `tts/librispeech_prompt_smoke/` |
| VAD | `vad/README.md` | `vad/librispeech_silence_smoke/` |
| VC | `vc/README.md` | `vc/librispeech_voice_conversion_smoke/` |
| Speech understanding | `speech_understanding/README.md` | Engine-bound suite containing every task above. |

## Selection Rules

1. Route by task first using
   `docs/agents/model_tool_agent/task_playbooks/ROUTING.md`.
2. Open only the matching task fixture index. For `speech_understanding`, open
   its composite index and all registry members.
3. Select 2-3 samples for phase-1 validation; keep at most 5 samples.
4. Copy selected fixture files into the model directory.
5. Record source fixture paths in `model.spec.yaml`, `spec_validation.json`, or
   `tool_agent_run_report.json`.

## Do Not

- Do not use this directory as a reason to delete model-local fixtures.
- Do not add multiple fixture sets for the same task without first deciding why
  the existing representative set is insufficient.
- Do not copy large benchmark datasets into this directory.
- Do not load all task fixture indexes by default unless the normalized task is
  the `speech_understanding` suite.
