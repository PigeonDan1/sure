# Repro Instructions

1. Read repro_manifest.json first, then environment_manifest.json, weights_manifest.json, and minimal_repro_spec.json.
2. Create a fresh temporary working directory outside the phase-1 model directory.
3. Create a fresh venv with /opt/anaconda3/envs/fireredvad/bin/python -m venv <temp>/.venv.
4. Install dependencies with <temp>/.venv/bin/pip install --cache-dir <recorded pip cache> -r reference/requirements.lock.txt.
5. Validate the fallback ffmpeg binary recorded in environment_manifest.json before running inference.
6. Validate the recorded weight file path from weights_manifest.json; re-download only if it is missing or unreadable.
7. Run the minimal reproduction command from minimal_repro_spec.json against fixtures/en_16k_10s.wav.
8. Repro succeeds only if the result is JSON-serializable, contains a non-empty text field, and is executed from the fresh temporary venv rather than the phase-1 .venv.
