# Silero VAD minimal repro bundle

## What this bundle reproduces
This bundle rebuilds the minimal CPU-only callable path for `silero-vad`:
1. import `silero_vad`
2. load the package-internal JIT model with `load_silero_vad(onnx=False)`
3. read a 16kHz mono WAV fixture
4. run `get_speech_timestamps`
5. validate the unified JSON output schema

## Files to read first
- `repro_manifest.json`
- `environment_manifest.json`
- `weights_manifest.json`
- `minimal_repro_spec.json`
- `fixture_manifest.json`

## Rebuild the minimal environment from zero
From a fresh copy of this directory, run:

```bash
env   UV_CACHE_DIR=./uv-cache   TMPDIR=./tmp   UV_PROJECT_ENVIRONMENT=./.repro_env   /Users/wency/.local/bin/uv sync --project . --python /opt/anaconda3/bin/python3.12 --locked
```

This replay requires network access to download wheels because the bundle does not vendor dependencies. Offline replay is not supported.

## Validate pip package and weights
- The tested package is `silero-vad==6.2.1`.
- The tested weight source is package-internal: `silero_vad.data/silero_vad.jit`.
- No torch hub cache is required or allowed for the successful path.

## Run the minimal reproduction
```bash
./.repro_env/bin/python minimal_repro_runner.py --audio fixtures/en_16k_10s.wav --output repro_output.json
./.repro_env/bin/python output_contract_check.py repro_output.json
```

## Success criteria
Replay is successful when:
- the environment rebuild completes,
- `minimal_repro_runner.py` generates `repro_output.json`,
- `output_contract_check.py` passes,
- and the output keeps the required schema fields.

## Empty segments policy
If `segments` is empty, replay can still be considered successful as long as the callable path succeeds and the schema contract passes. This bundle is for smoke reproducibility, not VAD accuracy scoring.

## Smoke reproducibility vs. accuracy validation
This bundle only proves the minimal callable path is reproducible. It does not claim benchmark accuracy, threshold tuning quality, long-audio support, or streaming VAD behavior.
