# WeSpeaker Minimal Reproduction

1. Create a fresh temporary working directory outside `/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/wespeaker`.
2. Copy `src/sure_eval/models/wespeaker/repro_bundle/` into that temporary directory.
3. Rebuild the runtime with:

   ```bash
   SURE_WESPEAKER_VENV_DIR="$PWD/.venv" \
   SURE_WESPEAKER_PIP_CACHE_DIR="$PWD/pip-cache" \
   /Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/wespeaker/setup.sh
   ```

4. Run the minimal reproduction with the copied tiny fixtures:

   ```bash
   PYTHONPATH="/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/wespeaker" WESPEAKER_HOME="/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/wespeaker/checkpoints" \
   "$PWD/.venv/bin/python" - <<'PY'
   from pathlib import Path
   from model import ModelWrapper

   bundle = Path.cwd() / 'repro_bundle' / 'fixtures'
   wrapper = ModelWrapper()
   result = wrapper.predict({
       'enroll_audio': str(bundle / 'spk1_enroll.wav'),
       'trial_audio': str(bundle / 'spk1_trial.wav'),
   })
   print(result.to_json())
   PY
   ```

5. Reproduction is successful when the JSON is serializable and contains `model_name`, `task`, `enroll_audio`, `trial_audio`, `score`, `score_is_finite`, `device`, and `error_code`, with a finite numeric `score`.

## Excluded Artifacts

The repro bundle intentionally excludes checkpoints, `.venv/`, `.runtime/`, host caches, large corpora, and the full upstream git snapshot. They are omitted to keep the bundle small, auditable, and aligned with the policy that large weights and datasets must not be repackaged into the smoke repro bundle.

## Weight Handling

Weights are not bundled. The bundle records the validated local path `/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/wespeaker/checkpoints/english` plus the local archive fallback `/Users/wency/Desktop/sjtu/SURE/sure/src/sure_eval/models/wespeaker/checkpoints/english/voxceleb_resnet221_LM.tar.gz`. If the local `config.yaml` is missing, `setup.sh` restores it from the existing tarball before validation.
