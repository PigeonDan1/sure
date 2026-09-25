# Speech enhancement

Use `task_type: se` for noisy speech to enhanced audio. This is an audio-output
task; an ASR transcript is not a successful SE prediction.

## Model and fixture

Read `fixtures/tasks/se/README.md`. The registered smoke fixture contains two
noisy/clean pairs. Stage it with `scripts/prepare_fixture.py`, preserving
`noisy_audio`, `reference_audio`, sample keys, and path provenance. Only noisy
audio enters the model. Clean audio and reference transcripts are scoring targets.

GTCRN is a small CPU-capable starting point. Its upstream repository is
https://github.com/Xiaobin-Rong/gtcrn. The sherpa-onnx implementation documents
`OfflineSpeechDenoiser` and the `gtcrn_simple.onnx` release asset in
https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/offline-speech-enhancement-gtcrn.py.
Research and pin the runtime and weight hash; distinguish the upstream PyTorch
checkpoint from this ONNX export. GitHub release weights use `release_or_pypi`.
Do not combine PyTorch import snippets with an ONNX runtime's load/inference API.

## Wrapper and MCP contract

- Load weights once in `ModelWrapper`, then reuse the denoiser for each call.
- Expose `enhance_speech` with `audio_path` and `output_path` in both the MCP
  input schema and `config.yaml`'s `tools[].input_schema`.
- `predict` writes a unique WAV under the requested output directory and returns
  `{"audio_path": "<absolute enhanced WAV>", "sample_rate": 16000}` for a 16 kHz
  model. `enhanced_audio` is also accepted by the inference normalization bridge.
- Respect the model's sample-rate/channel policy explicitly. Validate finite,
  nonempty samples, a readable audio file, and the actual output sample rate.
  Do not return the noisy input path as the enhanced result.
- Keep generated files outside the approved model bundle. A model-local `.venv`
  is only a validation runtime; follow normal sealing and approval for inference.

## Validation and scoring

Run import, load, inference, and contract checks on the registered pairs. Check
MCP initialize/list/call and compare its saved audio with direct inference.
Preserve any declared latency compensation; do not silently truncate, realign,
or rescale audio just to improve a metric.

`sure_infer` persists structured JSONL alongside the path TSV. `sure_eval` uses
the `samples_jsonl` bridge, with `enhanced_audio`, `reference_audio`, and
`noisy_audio` as distinct roles. Discover routes in the pinned evaluator before
selecting SI-SDR, STOI, PESQ, or an optional learned metric. Save per-sample
results and the unchanged noisy baseline. These fixtures are speech-on-speech
mixtures, so a denoiser need not improve every score; report model quality
separately from whether the workflow completed.

`sure_trans` currently requires PyTorch and registry-backed container delivery;
the sherpa-onnx adapter belongs in `sure_onboard`. `sure_agent_eval` supports a
single approved SE MCP stage with audio input/output and SE datasets; see its
`examples/agent_se_example.yaml`. Its gate checks the structured audio bundle.
Do not turn audio paths into text answers or attach a text API stage to an SE
agent. `examples/gtcrn/` contains a development adapter and reproducible backend
smoke test; its evidence does not replace runtime sealing or human approval.
