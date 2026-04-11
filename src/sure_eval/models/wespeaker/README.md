# Speaker Verification WeSpeaker Model

Speaker verification wrapper for the validated WeSpeaker English similarity path in SURE-EVAL.

## Model Information

| Attribute | Value |
|-----------|-------|
| **Name** | `wespeaker` |
| **Display Name** | `WeSpeaker-English-ResNet221_LM-SV` |
| **Task** | Speaker Verification |
| **Model ID** | `wenet-e2e/wespeaker` |
| **Repo** | `https://github.com/wenet-e2e/wespeaker` |
| **Pinned Commit** | `c92349a14d6b426808c4e09b8b12e076864dfc11` |
| **Deployment** | Local |
| **Validated Checkpoint** | `english` |
| **Backend** | `pip` |
| **Python** | `3.10` (actual successful run: `3.10.20`) |
| **Weights** | Built-in auto-download via `wespeaker.load_model("english")` |
| **Weight Cache** | `WESPEAKER_HOME=pretrained_models/wespeaker` |
| **Phase-1 Status** | Passed |

## Capabilities

- **Speaker verification**: compute a repo-native similarity score from an enrollment/trial audio pair
- **Repo-native validation target**: `import wespeaker` → `wespeaker.load_model("english")` → `model.compute_similarity(...)`
- **JSON contract**: returns a JSON-serializable object with numeric `similarity_score`
- **CPU path validated**: the successful phase-1 run used `model.set_device("cpu")`
- **Model-local reproducibility**: weights are cached under a model-local `WESPEAKER_HOME`

## Validated Phase-1 Scope

This onboarding validates only the minimal repo-native speaker verification path:

```python
import wespeaker

model = wespeaker.load_model("english")
model.set_device("cpu")
score = model.compute_similarity(
    "tests/fixtures/shared/speaker_verification/spk1_enroll.wav",
    "tests/fixtures/shared/speaker_verification/spk1_trial.wav",
)
```

Out of scope for this phase:

- diarization quality validation
- speaker registration workflows
- ONNX alternative runtimes
- PLDA or score calibration
- training or benchmark evaluation

## Fixtures

Phase-1 uses the task-specific shared speaker verification fixtures:

- `tests/fixtures/shared/speaker_verification/spk1_enroll.wav`
- `tests/fixtures/shared/speaker_verification/spk1_trial.wav`
- `tests/fixtures/shared/speaker_verification/spk2_trial.wav` (optional negative sanity-check)

The validated spec records the fixture as task-specific, and the discovery summary records the positive and negative pairs under the shared speaker verification fixture directory.

## Environment Setup

### Recommended Setup

```bash
cd /path/to/sure/src/sure_eval/models/wespeaker1.0
python -m venv .venv
source .venv/bin/activate

pip install 'setuptools<81'
pip install --no-build-isolation -r upstream/requirements.txt
pip install --no-build-isolation -e upstream
pip install --force-reinstall torch==2.1.2 torchaudio==2.1.2

export WESPEAKER_HOME="$(pwd)/pretrained_models/wespeaker"
```

### Important Notes

- The validated run used the upstream **development install route** (`requirements.txt` + editable install), not only a thin package install.
- A **recorded local patch** for lazy frontend imports was required during onboarding.
- No system package additions were required for the successful CPU path.

## Test Results

These are **phase-1 fixture sanity results**, not full benchmark metrics.

| Date | Validation Target | Result | Notes |
|------|-------------------|--------|-------|
| 2026-04-03 | Positive same-speaker pair | `0.9424791634082794` | Import, load, infer, and contract all passed |
| 2026-04-03 | Optional negative pair | `0.4905685018748045` | Used only as a sanity-check |

## Usage

### Direct Usage

```python
from model import ModelWrapper

wrapper = ModelWrapper({"model_name": "english", "device": "cpu"})
result = wrapper.predict(
    {
        "enrollment_audio": "tests/fixtures/shared/speaker_verification/spk1_enroll.wav",
        "trial_audio": "tests/fixtures/shared/speaker_verification/spk1_trial.wav",
    }
)
print(result.to_json())
```

### As MCP Server

```bash
cd /path/to/sure/src/sure_eval/models/wespeaker1.0
source .venv/bin/activate
python server.py
```

## API Reference

### Tools

- `speaker_verify(enrollment_audio, trial_audio)`: run the validated English similarity path on an audio pair
- `healthcheck()`: report whether the wrapper has loaded the model and whether the cache is present

### Wrapper Contract

Input:

```json
{
  "enrollment_audio": "path/to/enroll.wav",
  "trial_audio": "path/to/trial.wav"
}
```

Output:

```json
{
  "similarity_score": 0.9424791634082794,
  "enrollment_audio": "...",
  "trial_audio": "...",
  "model_name": "english",
  "device": "cpu",
  "cache_dir": "..."
}
```

## Onboarding Notes and Error History

This model **ultimately passed phase-1**, but the path was not clean. Keeping the failure history in the README is useful because the main risk was not fixture mismatch; it was the upstream import/runtime stack.

### What went wrong first

1. **Build environment issue**
   - The validated run had to pin `setuptools<81` and disable build isolation because `visdom` failed to build when `pkg_resources` was unavailable.

2. **Import-stage integration issue**
   - The first validation attempt failed at `VALIDATE_IMPORT`.
   - `import wespeaker` eagerly pulled optional frontend code paths that were not needed for the validated English fbank speaker verification target.
   - The recorded mitigation was a minimal local patch to make optional frontend modules lazy-imported instead of eager-imported.

3. **Inference-stage runtime compatibility issue**
   - After import/load started working and weights were downloaded, inference failed because `torchaudio 2.11.0` required `torchcodec` during audio loading.
   - The recorded mitigation was to pin `torch==2.1.2` and `torchaudio==2.1.2`.

### Why this matters

The important lesson from this onboarding is that the minimal speaker verification path itself was valid, but the surrounding dependency and import surface was broader than the actual phase-1 target. The final successful run used a pinned commit, the upstream development install route, a recorded lazy-import patch, and a targeted torch/torchaudio pin.

## Files

- `model.spec.yaml` - phase-1 model specification
- `validate_phase1.py` - import/load/infer/contract smoke validation
- `model.py` - thin wrapper around the validated WeSpeaker English similarity path
- `server.py` - MCP server exposing `speaker_verify` and `healthcheck`
- `__init__.py` - package exports
- `artifacts/verdict.json` - final phase-1 verdict
- `artifacts/validation.log` - retry and validation history
- `artifacts/build_plan.json` - executable build and validation plan
- `artifacts/weights_manifest.json` - downloaded checkpoint record
- `artifacts/artifact_manifest.json` - artifact inventory

## Notes

- This README describes the **validated SURE wrapper path**, not the full upstream WeSpeaker feature surface.
- The successful run did **not** require GPU.
- The successful run did require a recorded local patch and targeted runtime pinning.
- The final run passed without triggering escalation.

## See Also

- `artifacts/verdict.json`
- `artifacts/validation.log`
- `artifacts/build_plan.json`
- `artifacts/weights_manifest.json`
- `artifacts/artifact_manifest.json`
