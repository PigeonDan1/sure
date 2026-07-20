# Harness Local Model Compatibility

This directory records local-only compatibility artifacts for bootstrapping the
harness branch against the runnable SURE-EVAL sandbox.

## Current Strategy

- Keep harness code and skill state machines in this repo.
- Reuse runnable model deployment units from an existing SURE-EVAL sandbox by
  setting `LEGACY_SURE_EVAL_ROOT` / `SURE_MODELS_DIR`, or by creating local
  symlinks under `sure/models/`.
- Use symlinks under `sure/models/` for fast local execution.
- Do not commit absolute symlinks or readiness JSON to GitHub; commit resolver
  code, docs, tests, and portable examples only.

## Selected Models

- `nvidia__parakeet-rnnt-1.1b`: ASR, static runtime readiness is `ready`.
  MCP healthcheck passes. Real `transcribe_audio` passes with `DEVICE=cpu`.
  GPU loading currently fails because the installed torch runtime expects a
  newer NVIDIA driver than this host exposes.
- `rednote-hilab__dots.tts-base`: TTS, static runtime readiness is `ready`.
  MCP `synthesize_speech` passes on CPU when `CUDA_VISIBLE_DEVICES` is hidden.
  A plain `DEVICE=cpu` run still lets the underlying runtime select CUDA and
  can OOM on a busy GPU, so harness CPU runs now hide CUDA automatically.
- `Qwen__Qwen3-ASR-1.7B`: ASR, discoverable with verdict, but blocked by
  missing `.venv/bin/python` in the current model directory.

## Useful Commands

```bash
python3 sure/skills/sure_eval/scripts/resolve_model_dir.py \
  --model nvidia__parakeet-rnnt-1.1b \
  --require-verdict \
  --require-runtime-files
```

```bash
python3 - <<'PY'
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path("sure/skills/sure_eval/scripts").resolve()))
from sure_eval.inference import get_runtime_readiness
from sure_eval.models.registry import ModelRegistry

registry = ModelRegistry()
for name in ["nvidia__parakeet-rnnt-1.1b", "rednote-hilab__dots.tts-base"]:
    report = get_runtime_readiness(registry.get_model(name), model_name=name, models_dir=registry.models_dir)
    print(name, report["status"], report["failure_class"])
PY
```

The ASR ready model has a dataset-level CPU smoke under:

```text
sure/compat/server_prediction_smoke/nvidia__parakeet-rnnt-1.1b/asr_en_server_smoke/
```

That run exercises model symlink discovery, model-local MCP server launch,
`generate_predictions_via_server.py`, prediction validation, and external
`sure-evaluation` scoring. The generated transcript is `lobster olenuburg` and
the external ASR WER score is `0.0` against the local smoke reference.

The TTS ready model has a dataset-level CPU smoke under:

```text
sure/compat/server_prediction_smoke/rednote-hilab__dots.tts-base/tts_en_server_smoke/
```

That run exercises model symlink discovery, model-local MCP server launch,
`generate_predictions_via_server.py`, generated audio output, prediction
validation, and external `sure-evaluation` DNSMOS scoring. The generated WAV is
mono 48 kHz and is referenced by the structured prediction JSONL. The external
TTS metric route is `tts.en.multi.audio_metric_nodes` with `scoring/dnsmos`.

Next execution step: repeat the same bounded prediction smoke on a GPU host with
a driver/runtime pair compatible with the model-local torch build.

## Evaluation Engine

Local evaluation resolves to the conventional external-engine submodule by
default:

```text
sure/external/sure-evaluation
```

`SURE_EVALUATION_HOME` remains available as an explicit local override, but the
workspace checkout is not an implicit fallback. `resolve_evaluation_engine.py`
validates the selected engine and describes the
`asr.en.wer.whisper_norm.wenet_wer` pipeline. A single-fixture ASR WER smoke has
been run under `sure/compat/evaluation_smoke/asr_en_wer/`.

`evaluate_predictions.py` now supports `--evaluation-backend auto|external|legacy`.
`auto` prefers the standalone `sure-evaluation` engine when it is available and
falls back to the vendored legacy evaluator only when the task is not bridged or
the engine is absent. A harness-level bridge smoke is recorded under:

```text
sure/compat/evaluation_bridge_smoke/
```

That smoke validates:

- dataset resolution through `DatasetManager`
- prediction-file validation
- external `sure-evaluation` execution
- `evaluation_payload.json` output
- standardized `results/<model>/<protocol>/report.jsonl`
- external pipeline artifacts under `run/external_evaluation/`

In this git-backed harness worktree, `sure/external/sure-evaluation` should be
a local checkout ignored by Git. Local model links and runtime smoke artifacts
stay ignored.
