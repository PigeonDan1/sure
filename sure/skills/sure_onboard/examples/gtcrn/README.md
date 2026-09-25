# GTCRN SE adapter and backend smoke test

This example uses GTCRN through **sherpa-onnx 1.13.8**, CPU, mono 16 kHz.
It is an onboarding starting point, not an approved or complete deployment bundle.
`model_input.yaml` records the researched ONNX route; do not combine the upstream
PyTorch import with the sherpa-onnx loading API.

Sources: [GTCRN](https://github.com/Xiaobin-Rong/gtcrn),
[sherpa-onnx example](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/offline-speech-enhancement-gtcrn.py).
Weight SHA-256: `e77603ac0c23dac3227dd2d7135b3a585cbee2679048aecfa886657d3ae1b534`.
The weight file is downloaded separately; it is not included in this example.

## Reproduce on Windows

From the repository root, with `uv` available:

```powershell
$example = 'sure/skills/sure_onboard/examples/gtcrn'
$model = 'sure/models/gtcrn'
New-Item -ItemType Directory -Force "$model/checkpoints" | Out-Null
Copy-Item "$example/model.py", "$example/config.yaml", "$example/validate.py", "$example/requirements.lock.txt" $model
uv venv --python 3.11 "$model/.venv"
$modelPython = "$model/.venv/Scripts/python.exe"
uv pip sync --python $modelPython --require-hashes "$model/requirements.lock.txt"
curl.exe -fL https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/gtcrn_simple.onnx -o "$model/checkpoints/gtcrn_simple.onnx"
if ((Get-FileHash "$model/checkpoints/gtcrn_simple.onnx" -Algorithm SHA256).Hash.ToLower() -ne 'e77603ac0c23dac3227dd2d7135b3a585cbee2679048aecfa886657d3ae1b534') { throw 'Checkpoint hash mismatch' }
```

Prepare the repository's locked Harness and Evaluation Runtimes using their
normal bootstrap commands. Preserve the Harness Runtime environment binding
(`HARNESS_PYTHON_BIN` and `SURE_HARNESS_*`) when invoking the integration test:

```powershell
& $env:HARNESS_PYTHON_BIN "$example/smoke.py" --model-dir $model --model-python $modelPython --output-dir .sure/runs/gtcrn-se-smoke
```

Use a fresh output directory each time. Linux uses `.venv/bin/python` instead.
For an additional pair, pass `--fixture <directory>` containing audio files and
`gt.jsonl` rows with `key`, `noisy_audio`, and `reference_audio` relative paths.
The test writes an isolated site policy under its output directory and exercises:

1. MCP initialize and tools/list, including the `output_path` schema.
2. Direct real inference on both registered SE fixture pairs.
3. Source projection without text annotations and real MCP inference through the
   agent runner, with byte-for-byte comparison against direct results.
4. Execution and evaluation gates, the pinned SI-SDR route, and the unchanged
   noisy baseline. See `smoke-report.json` and the per-sample evaluation reports.

The runner's plan is a development test fixture; this is not evidence that
`resolve_agent` approved the model or that a skill reached its terminal state.
Production usage still requires the normal onboarding artifacts, a bundle-local
server command, runtime inventory, `/sure_approve` decision, and approved stage
resolution. Use `package=none device=cpu` only on sites allowing local Python.
The model-local venv is not the sealed runtime; normal onboarding seals it with
`materialize_model_runtime.py` and validates it with `check_env.py`.

## Audio contract and limits

Only noisy input enters the model. Clean reference audio stays in the scoring
projection. Output is PCM16 WAV at the requested path. Native upstream STFT/iSTFT
length is preserved (the two fixtures return 59,904 and 86,528 frames, compared
with 60,000 and 86,640 input frames); the wrapper adds no padding or alignment.
The pinned SI-SDR provider compares the common length of each audio pair.

On the two speech-on-speech smoke mixtures, measured mean SI-SDR was **17.1492 dB**
versus **18.5637 dB** for the noisy baseline. These are workflow checks, not evidence
of a quality gain or a general benchmark. Other metrics and larger datasets were
not measured. `sure_trans` requires PyTorch, so this ONNX route uses `sure_onboard`.

An additional held-out utterance, `2094-142345-0026.wav` from the repository's
LibriSpeech gender fixture, was tested with synthetic Gaussian white noise at
5 dB SNR (`numpy.default_rng(20260925)`). Its SI-SDR improved from **5.0004 dB**
to **12.7446 dB**; direct/MCP equality and both bundle gates passed. This single
synthetic-noise case does not establish general enhancement quality.
