# s2tt_nllb Repro Instructions

1. Copy this `repro_bundle/` into a fresh temporary working directory.
2. Read `weights_manifest.json` before running anything.
   - GitHub-safe repro does not include model weights in `repro_bundle/`.
   - Fresh-clone replay must either download weights from Hugging Face or reuse a prepopulated model-local cache at the restore targets listed in `weights_manifest.json`.
   - Do not expect `repro_bundle/weights/` to exist.
3. Verify the local environment prerequisites:
   - `python3 --version`
   - confirm that network access is available for Hugging Face, or that the model-local checkpoint/cache paths in `weights_manifest.json` are already populated
4. Recreate the minimal environment from scratch inside the temporary directory:
   - `python3 -m venv .venv`
   - `.venv/bin/pip install -r ./requirements.phase1.txt`
5. Restore or validate weights using `weights_manifest.json`.
   - If the restore targets under `src/sure_eval/models/s2tt_nllb/checkpoints/` already contain the required files, validate them against the recorded identifiers and checksums.
   - Otherwise, download `facebook/nllb-200-distilled-600M` and `openai/whisper-tiny` from Hugging Face into those model-local checkpoint paths.
6. Run the minimal reproduction:
   - `.venv/bin/python ./minimal_repro.py --fixture /absolute/path/to/tests/fixtures/shared/asr/en_16k_10s.wav --source-lang eng_Latn --target-lang zho_Hans --output ./outputs/sample_output.json`
7. Judge success using the output JSON only:
   - the file exists and is valid JSON
   - `text`, `translation_text`, and `asr_text` are present and non-empty
   - `source_lang` is `eng_Latn`
   - `target_lang` is `zho_Hans`
   - `error_code` is `null`
8. If reproduction fails without network and without preloaded weights, classify it as `missing_weights`, not as an integration failure.
9. If reproduction fails for another reason, capture stderr and compare it against `weights_manifest.json`, `environment_manifest.json`, and `minimal_repro_spec.json` before changing anything.
