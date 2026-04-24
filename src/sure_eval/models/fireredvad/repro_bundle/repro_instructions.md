# FireRedVAD Repro Bundle

1. Read `repro_manifest.json` first. It records the tested upstream commit, selected backend, replay env seed, bundled weights path, and all other manifests.
2. Copy this `repro_bundle/` into a fresh temp directory. Do not reuse the original phase-1 working directory as the replay directory.
3. Materialize the bundle-local replay environment by copying `repro_bundle/venv_seed` to `.venv-repro` inside the temp directory.
4. Verify that the fixture path in `fixture_manifest.json` still exists and that `repro_bundle/weights/FireRedVAD/VAD` contains `model.pth.tar` and `cmvn.ark`.
5. Run `.venv-repro/bin/python repro_bundle/support/repro_validate.py` with:
   `SURE_REPRO_FIXTURE=/Users/wency/Desktop/sjtu/SURE/sure/tests/fixtures/shared/vad/en_16k_10s.wav`
   `SURE_REPRO_WEIGHTS_DIR=<temp>/repro_bundle/weights/FireRedVAD/VAD`
   `SURE_REPRO_OUTPUT=<temp>/repro_output.json`
6. Repro is successful only if the script exits 0, writes `repro_output.json`, and the JSON still contains `dur`, non-empty `timestamps`, and `wav_path`.
