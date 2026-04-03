# WeSpeaker Model for SURE-EVAL

WeSpeaker speaker verification phase-1 onboarding record for SURE-EVAL.

## Model Information

- **Model**: [wenet-e2e/wespeaker](https://github.com/wenet-e2e/wespeaker)
- **Fixed Commit**: `c92349a14d6b426808c4e09b8b12e076864dfc11`
- **Runtime Target**: `wespeaker.load_model("english")`
- **Display Name**: `WeSpeaker-English-ResNet221_LM-SV`
- **Task**: Speaker Verification
- **Deployment**: Local
- **Phase-1 Status**: Passed

## Phase-1 Target

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
- ONNX runtime alternative paths
- PLDA or score calibration
- training or benchmark evaluation

## Fixture

Phase-1 uses the task-specific shared speaker verification fixtures:

- `tests/fixtures/shared/speaker_verification/spk1_enroll.wav`
- `tests/fixtures/shared/speaker_verification/spk1_trial.wav`
- `tests/fixtures/shared/speaker_verification/spk2_trial.wav` for optional negative sanity-check

All three files are local mono 16kHz PCM WAV files.

## Execution Summary

The successful run followed:

`DISCOVER -> CLASSIFY -> PLAN -> VALIDATE_SPEC -> BUILD_ENV -> FETCH_WEIGHTS -> VALIDATE_IMPORT -> VALIDATE_LOAD -> VALIDATE_INFER -> VALIDATE_CONTRACT -> GENERATE_WRAPPER -> SAVE_ARTIFACTS`

Environment/build choices:

- backend: `pip`
- actual Python: `3.10.20`
- model-local venv: `src/sure_eval/models/wespeaker1.0/.venv`
- model-local cache target: `src/sure_eval/models/wespeaker1.0/pretrained_models/wespeaker`
- install route: upstream `requirements.txt` + editable install from the fixed local source copy

Targeted mitigations that were required:

1. Pin upstream to commit `c92349a14d6b426808c4e09b8b12e076864dfc11`
2. Retry the dev install with `setuptools<81` and `--no-build-isolation` because `visdom` failed to build when `pkg_resources` was unavailable
3. Apply a recorded minimal local patch so `wespeaker.frontend` lazily imports optional frontends instead of eager-importing `s3prl`, `whisper`, and `w2vbert`
4. Pin `torch==2.1.2` and `torchaudio==2.1.2` after `torchaudio 2.11.0` required `torchcodec` for `torchaudio.load()`

## Final Validation Result

- positive similarity score: `0.9424791634082794`
- optional negative similarity score: `0.4905685018748045`
- output contract: JSON-serializable object with numeric `similarity_score`
- device: `cpu`

## Classification

- Primary issue class encountered during this onboarding: `integration` first, then `dependency`
- Fixture mismatch was not the blocker
- Upstream `requirements.txt` is recommended over the thin package install for reproducibility
- A local lazy-import patch was required for this fixed commit
- The final solution is reproducible without changing the host global environment
