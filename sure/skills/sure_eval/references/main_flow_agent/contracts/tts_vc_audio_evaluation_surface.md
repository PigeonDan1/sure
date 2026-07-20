# TTS/VC Audio Evaluation Surface Contract

## Purpose

TTS and VC runs have two independent execution surfaces:

- **inference surface**: runs the model server/tool and writes prediction
  artifacts
- **evaluation surface**: consumes validated prediction artifacts and runs the
  audio metric pipeline through `src/sure_eval/evaluation` using node-local uv
  environments

This is an audio-runtime specialization of the global evaluation-router rule.
TTS/VC metrics are not a separate evaluation implementation: the evaluation
surface must still call `scripts/evaluate_predictions.py` or
`sure_eval.evaluation.scripts.run_task(...)`, select routes from
`src/sure_eval/evaluation/tasks/tts/routes.yaml` or
`src/sure_eval/evaluation/tasks/vc/routes.yaml`, and preserve the resulting
`pipeline_description.json` and `report.json` artifacts.

This contract prevents the main flow from treating either a model inference
image or a per-metric Docker image as the metric dependency surface. A model can
generate correct audio while still lacking metric runtime tools, and a vc
evaluation container may provide only shell/interpreter/CUDA/`ffmpeg` while the
actual Whisper, Paraformer, MOS, and speaker-embedding dependencies come from
the node-local uv environments under `src/sure_eval/evaluation/nodes/*/.venv`.

## Artifact Boundary

The inference surface is complete when these artifacts exist and validation
passes:

```text
predictions/<dataset>.txt
predictions/<dataset>.jsonl
predictions/logs/<dataset>.log
prediction_generation_status.json
validation_payload.json
```

After this point, a TTS/VC evaluation retry must not regenerate audio unless
the prediction artifacts are invalid or explicitly stale.

During inference, the model tool call must receive available prompt/reference
text fields from the canonical samples (`prompt_text`, `ref_text`,
`reference_text`, or task-defined equivalents). Missing prompt/reference text
can trigger ASR fallback in some TTS models and should be assessed as an input
handoff/runtime-surface problem before concluding the model is broken.

When no metric is explicitly requested, the audio evaluation surface must run
the full task-appropriate audio metric suite, not only the semantic
ASR-derived metric.

For TTS, the default suite is:

```text
tts_wer or tts_cer by dataset language
sim/wavlm-large
sim/ecapa-tdnn
sim/eres2net
dnsmos
wv-mos
utmos
```

For VC, the default suite is:

```text
vc_wer or vc_cer by dataset language
sim/wavlm-large
sim/ecapa-tdnn
sim/eres2net
dnsmos
wv-mos
utmos
```

Semantic audio metrics are language-routed. For TTS, Chinese-family languages
(`zh`, `cmn`, `yue`) use `tts_cer`; English uses `tts_wer`. For VC,
Chinese-family languages use `vc_cer`; English uses `vc_wer`. If the inference
surface writes an audio `evaluation_handoff.json` without an explicit metric, it
must derive the semantic metric from the canonical dataset language and append
the speaker-similarity and MOS metrics above instead of hard-coding `tts_wer`
or `vc_wer`. Running only `tts_wer` / `vc_wer` is incomplete unless the user
explicitly requested that narrow metric set.

The evaluation surface consumes those artifacts and produces:

```text
evaluation_payload.json
report.jsonl
protocol.yaml
metrics/<dataset>/<metric_slug>/report.json
metrics/<dataset>/<metric_slug>/pipeline_description.json
sample_reports/<dataset>/<metric_slug>.jsonl
report_snapshot.md
evaluation_only_status.json
```

## Required Evaluation Surface

For TTS/VC tasks, `run_single_model.sh` and
`run_single_model_single_dataset.sh` must write `evaluation_handoff.json` after
prediction validation.

This rule applies only to datasets whose canonical JSONL declares task `TTS` or
`VC`. Non-audio datasets in the same run must continue through the normal
`evaluate_predictions.py` path. A pure ASR/S2TT/SER/GR/SLU/SD/SA-ASR run must
not enter the audio evaluation handoff path.

If every dataset in the run is TTS/VC, the inference surface stops after
writing the handoff. If the run mixes TTS/VC with non-audio datasets, the
inference surface evaluates only the non-audio datasets and leaves the audio
datasets for the evaluation-only surface.

The next step must materialize `templates/run_audio_evaluation_only.sh` into
the run directory and run it locally from the repository checkout by default.
The evaluation-only job must set `SURE_TTS_AUDIO_RUNTIME=node_local` and call
`scripts/evaluate_predictions.py`, which routes heavy audio metrics through
`src/sure_eval/evaluation/audio_runtime.py` and the node-local providers.

Do not submit the TTS/VC evaluation-only surface through `vc submit` merely
because `vc` is available. Use `vc submit` only when the user explicitly
requests cluster execution, or when local node-local preflight fails and the
blocker is recorded in the run evidence.

The `vc submit -i ...` image for evaluation, when a cluster submission is
explicitly required, is a base runtime only. It must provide the shell,
interpreter targets used by node-local venv symlinks when needed, CUDA/driver
access, and common tools such as `ffmpeg`; it must not be treated as the source
of metric dependencies or as the route selector for audio metrics. The metric
dependency surface and route of record are the repository tools under
`src/sure_eval/evaluation`.

If a node-local venv symlink points at an interpreter missing from the chosen
execution environment, first fix that execution surface and record the evidence.
Do not fall back to the model inference image or to per-metric Docker dependency
selection when the node-local uv workflow is available.

The full default TTS/VC suite may be segmented by metric family for runtime
duration or failure isolation, but each segment must call the same repository
evaluation workflow and node-local providers. In particular:

- `sim/eres2net` requires a ModelScope/3D-Speaker runtime with `oss2` and an
  audio loading path that does not fail on `libsox.so`/`torchaudio_sox`.
- `utmos` may require a Python interpreter version matching its node-local uv
  environment.
- `dnsmos` and `wv-mos` require their node-local checkpoints and providers.

If the suite is segmented by metric family, each segment must write the same
canonical metric artifact layout under `metrics/<dataset>/<metric_slug>/`, then
the final evaluation surface must merge all requested metrics back into one
run-local `evaluation_payload.json`, `report.jsonl`, `protocol.yaml`, and
`results/<model>/<protocol>/` mirror. A segment-level `Completed` status or a
partial `evaluation_payload.json` is not a completed TTS/VC evaluation.

## Preflight

Before full evaluation, the evaluation-only surface must check:

- `ffmpeg` is on `PATH`
- the controller Python can import `sure_eval` and `yaml`; heavy dependencies
  such as `torch` are validated inside the requested node-local metric
  runtimes, not as a controller-process requirement
- for `tts_wer` / `vc_wer`, Python can import the Whisper/Transformers runtime
  required by the configured transcription node
- for `tts_cer` / `vc_cer`, Python can import the FunASR/Paraformer runtime
  required by the configured transcription node
- the configured transcription runtime/cache exists for requested semantic
  metrics (`tts_wer`, `tts_cer`, `vc_wer`, `vc_cer`)
- speaker similarity and MOS provider runtimes are available for requested
  `sim/*`, `dnsmos`, `wv-mos`, and `utmos` metrics, or the run report records a
  structured provider blocker instead of marking the TTS/VC evaluation complete
- for `sim/eres2net`, the preflight must execute at least one real pair through
  the selected ERes2Net runtime; import-only checks can miss `oss2`,
  `libsox.so`, and `torchaudio_sox` failures
- for `dnsmos`, `wv-mos`, and `utmos`, the preflight must execute at least one
  real generated-audio sample through the selected MOS runtime
- requested semantic metrics match the dataset language route (`tts_cer` /
  `vc_cer` for Chinese-family languages; `tts_wer` / `vc_wer` for English)
- semantic metrics must run a one-sample transcription probe in the evaluation
  image before the full dataset evaluation; a pure import probe is not enough
  because optional dependency import chains can fail only when the transcription
  pipeline is constructed
- `validation_payload.json` has `is_valid: true`
- each requested dataset has both `.txt` and `.jsonl` prediction files

For `tts_wer`/`vc_wer`, `ffmpeg` failure is an evaluation-surface failure. It
is not evidence that the TTS/VC model itself is broken.

For inference failures, the model-server log must preserve the full stderr
traceback. High-level wrapper errors such as import failure summaries are not
enough evidence for assessment unless the underlying traceback is present.

## Evaluation Execution Rule

TTS/VC evaluation is defined by the repository workflow under
`src/sure_eval/evaluation`, not by Docker images. The evaluation-only step must
call `scripts/evaluate_predictions.py` and route heavy audio metrics to the
node-local uv environments and checkpoints under
`src/sure_eval/evaluation/nodes/*`.

The default execution surface is the local repository checkout with those
node-local uv environments and local checkpoints. If the user explicitly
requests cluster execution, or if local node-local preflight fails and the
blocker is recorded, the same repository workflow may be executed through
`vc submit`. In that case the container image is only the execution shell/base
runtime: it provides shell, CUDA/driver visibility, `ffmpeg`, and interpreter
targets required by node-local `.venv/bin/python` symlinks. It must not provide
or replace metric dependencies.

Any evaluation-only execution, local or via `vc submit`, must preserve the same
repository mount point used by inference if prediction files contain absolute
audio paths. In this project, generated TTS prediction paths commonly use:

```text
/workspace/sure-eval/...
```

Therefore any containerized evaluation-only run must mount the repository to
`/workspace/sure-eval` as well.

## Completion Criteria

A `vc` job status of `Completed` is not sufficient. A TTS/VC main-flow run is
successful only when the evaluation artifacts above exist and
`evaluation_payload.json` contains a successful metric result.

If inference completed and validation passed but evaluation artifacts are
missing, the run status is:

```text
prediction_complete_evaluation_incomplete
```

The required next action is an evaluation-only retry using this contract.

## Assessment Rules

The assessment must record:

- inference job id/task id/image/partition
- evaluation job id/task id/image/partition, when launched
- validation sample counts
- metric artifact paths
- whether prompt/reference text was available and forwarded for TTS/VC
  inference
- the underlying traceback for model-server import or tool-call failures
- any failing log excerpt from the evaluation surface

If the cluster/base runtime lacks `ffmpeg` or an interpreter required by a
node-local uv environment, classify the issue as an evaluation execution-surface
blocker, not a model bad case and not a reason to switch to model-inference or
per-metric Docker dependencies.
