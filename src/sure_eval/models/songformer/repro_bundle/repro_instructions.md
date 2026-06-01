# SongFormer Repro Bundle

1. Read `repro_manifest.json` first. It records the tested repo commit, selected backend, bundled snapshot path, bundled HF cache path, and all other manifests.
2. Copy this `repro_bundle/` into a fresh temp directory. Do not reuse the original phase-1 working directory as the replay directory.
3. Materialize the bundle-local replay environment by copying `repro_bundle/venv_seed` to `.venv-repro` inside the temp directory.
4. Verify that the fixture path in `fixture_manifest.json` still exists and that `repro_bundle/weights/SongFormer/model.safetensors` is present.
5. Run `.venv-repro/bin/python repro_bundle/support/repro_validate.py` with:
   `SURE_REPRO_FIXTURE=/Users/wency/Desktop/sjtu/SURE/sure/tests/fixtures/songformer/song_24k.wav`
   `SURE_REPRO_WEIGHTS_DIR=<temp>/repro_bundle/weights/SongFormer`
   `SURE_REPRO_HF_HOME=<temp>/repro_bundle/hf_cache`
   `SURE_REPRO_OUTPUT=<temp>/repro_output.json`
6. Repro is successful only if the script exits 0, writes `repro_output.json`, and the JSON is still a non-empty list of segments whose items contain `start`, `end`, and non-empty `label` fields.
