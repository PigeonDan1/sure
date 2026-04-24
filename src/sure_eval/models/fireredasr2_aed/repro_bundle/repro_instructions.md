# FireRedASR2-AED Repro Bundle

1. Read `repro_manifest.json` first. It points to the tested upstream commit, selected backend, resolved weights path, and all other manifests.
2. Create a fresh temp working directory and copy this `repro_bundle/` into it. Do not reuse the original phase-1 `.venv`.
3. Build a new Python 3.10 environment with `/opt/anaconda3/envs/fireredvad/bin/python -m venv .venv`.
4. Install the minimal replay dependencies with `.venv/bin/pip install -r repro_bundle/support/requirements.phase1.txt`.
5. Verify that the recorded weights directory in `weights_manifest.json` is still readable and that the shared fixture in `fixture_manifest.json` still exists.
6. Run `.venv/bin/python repro_bundle/support/repro_validate.py` with:
   `SURE_REPRO_UPSTREAM_ROOT=<temp>/repro_bundle/source_snapshot/FireRedASR2S`
   `SURE_REPRO_WEIGHTS_DIR=/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/fireredasr2_aed/pretrained_models/FireRedASR2-AED`
   `SURE_REPRO_FIXTURE=/Users/wency/Desktop/sjtu/SURE/sure/tests/fixtures/shared/asr/en_16k_10s.wav`
   `SURE_REPRO_OUTPUT=<temp>/repro_output.json`
7. Repro is successful only if the script exits `0`, writes `repro_output.json`, and that JSON contains required fields `uttid` and non-empty `text`.
