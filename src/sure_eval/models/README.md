# Onboarded Model Directories

This directory contains SURE-EVAL model wrappers that can be routed by the
model tool-agent and the main-flow agent.

The clean model sync keeps source files, configs, setup scripts, Docker entry
points, and small fixtures in Git. Local runtime products stay out of Git:
`.venv/`, `.runtime/`, `checkpoints/`, `artifacts/`, `docker_artifacts/`,
`eval_runs*/`, caches, predictions, and metric reports.

Dataset payloads are also local-only. The top-level `data/` directory is
reserved for local mounts, symlinks, caches, and smoke data. Tracked dataset
registry updates should live under `config/` or documentation instead.

## Clean Model Set

The following directories were reconciled from validated clean model outputs:

| Task | Model directory | Status note |
|---|---|---|
| TTS/f5tts | `SWivid__F5-TTS_Emilia-ZH-EN` | local uv and Docker GPU contract evidence passed |
| TTS/indextts2 | `IndexTeam__IndexTTS-2` | local uv and Docker GPU contract evidence passed |
| TTS/cosyvoice3 | `FunAudioLLM__Fun-CosyVoice3-0.5B-2512` | strict local deployment closure present |
| TTS/qwen3tts | `Qwen__Qwen3-TTS-12Hz-1.7B-Base` | Qwen3 TTS 1.7B Base verdict passed |
| TTS/voxCPM2 | `openbmb__VoxCPM2` | strict local deployment closure present |
| TTS/dots.tts | `rednote-hilab__dots.tts-base` | local import/load/infer contract evidence passed |
| ASR/sensevoice-small | `asr_sensevoice_small` | local validation evidence passed |
| ASR/qwen3-asr-1.7b | `asr_qwen3` | local uv GPU validation evidence passed |
| ASR/parakeet-1.1b | `nvidia__parakeet-rnnt-1.1b` | phase-1 local validation passed; full local deployment closure pending |
| ASR/whisper-large-v3 | `whisper_large_v3_turbo` | reonboard verdict passed |
| ASR/moss-transcribe-preview-2b | `OpenMOSS-Team__MOSS-Transcribe-preview-2B` | workflow completed with local validation artifacts |
| ASR/granite-speech-4.1-2b | `ibm-granite__granite-speech-4.1-2b` | local validation evidence passed |

Known blocked candidates such as `Qwen__Qwen3-TTS-12Hz-0.6B-Base` and
`Qwen__Qwen3-TTS-12Hz-1.7B-VoiceDesign` are not part of this clean set until
their validation verdicts close.
