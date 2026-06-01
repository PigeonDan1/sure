# librosa repro bundle

1. Copy this `repro_bundle/` directory into a fresh temporary working directory.
2. Confirm the required files exist: `repro_manifest.json`, `weights_manifest.json`, `environment_manifest.json`, `minimal_repro_spec.json`, `fixture_manifest.json`, `environment_snapshot.txt`, and `repro_instructions.md`.
3. Create a fresh environment with `python3.12 -m venv .venv-repro`.
4. Install dependencies offline from the bundle: `./.venv-repro/bin/pip install --no-index --find-links ./wheelhouse -r ./requirements.phase1.txt`.
5. Validate the bundled fixture path in `fixture_manifest.json` and confirm the checksum if desired.
6. Run the minimal reproduction: `./.venv-repro/bin/python ./minimal_repro.py ./fixtures/en_16k_10s.wav`.
7. Success means the command prints JSON, `feature_type` is `mfcc`, and all required fields listed in `minimal_repro_spec.json` are present and non-empty where required.
8. If replay fails, record the stdout/stderr and write the blocker into `repro_validation.log` and `repro_verdict.json`.
