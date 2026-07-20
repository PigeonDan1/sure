# Shared Task Fixture Index

This directory is the canonical library and index for reusable task fixtures.
Each task keeps one representative smoke fixture set. Existing model-local
fixtures remain valid and must not be deleted during this indexing phase.

## Purpose

The model tool-agent should choose fixture candidates from this task-level index
and then copy the selected files into the model-local validation directory:

```text
sure/models/<model>/fixture/<task>/
```

This keeps validation reproducible while avoiding context-heavy searches across
all model directories.

## Task Index

| Task | Fixture index | Current source examples |
|------|---------------|-------------------------|
| ASR | `asr/README.md` | `asr/qwen3_asr_smoke/` copied from `src/sure_eval/models/asr_qwen3/fixture/asr/` |
| S2TT | `s2tt/README.md` | `s2tt/kimi_audio_s2tt_smoke/` copied from `src/sure_eval/models/asr_kimi_audio/fixture/s2tt/` |
| SER | `ser/README.md` | `ser/kimi_audio_ser_smoke/` copied from `src/sure_eval/models/asr_kimi_audio/fixture/ser/` |
| SLU | `slu/README.md` | `slu/kimi_audio_slu_smoke/` copied from `src/sure_eval/models/asr_kimi_audio/fixture/slu/` |
| GR | `gr/README.md` | `gr/kimi_audio_gr_smoke/` copied from `src/sure_eval/models/asr_kimi_audio/fixture/gr/` |
| SD | `sd/README.md` | `sd/librispeech_2spk_smoke/` with local two-speaker LibriSpeech audio and speaker-segment `gt.jsonl`. |
| SA-ASR | `sa_asr/README.md` | `sa_asr/librispeech_2spk_smoke/` with local two-speaker LibriSpeech audio and speaker-attributed transcript `gt.jsonl`. |
| Speech understanding | `speech_understanding/README.md` | Composite routing index that points to ASR/S2TT/SER/SLU/GR/SD/SA-ASR atomic fixtures. |
| TTS | `tts/README.md` | `tts/indextts2_zh_smoke/` copied from `src/sure_eval/models/IndexTeam__IndexTTS-2/fixture/zh/` |
| VC | `vc/README.md` | `vc/seed_vc_zh_smoke/` copied from `src/sure_eval/models/Plachtaa__seed-vc/fixture/zh/` |
| KWS | `kws/README.md` | `kws/wenwen_smoke/` copied from `src/sure_eval/models/daydream_factory__keyword-spot-fsmn-ctc-wenwen/fixture/kws/` |

## Selection Rules

1. Route by task first using
   `docs/agents/model_tool_agent/task_playbooks/ROUTING.md`.
2. Open only the matching task fixture index.
3. Select 2-3 samples for phase-1 validation; keep at most 5 samples.
4. Copy selected fixture files into the model directory.
5. Record source fixture paths in `model.spec.yaml`, `spec_validation.json`, or
   `tool_agent_run_report.json`.

## Do Not

- Do not use this directory as a reason to delete model-local fixtures.
- Do not add multiple fixture sets for the same task without first deciding why
  the existing representative set is insufficient.
- Do not copy large benchmark datasets into this directory.
- Do not load all task fixture indexes by default.
