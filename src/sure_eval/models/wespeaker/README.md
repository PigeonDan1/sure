# WeSpeaker

This model directory validates the **speaker_verification** smoke path for `Wespeaker/wespeaker-voxceleb-resnet221-LM` through the upstream WeSpeaker Python API. The phase-1 target is strictly `import wespeaker -> wespeaker.load_model("english") -> model.compute_similarity(enroll_audio, trial_audio)` on a tiny audio pair.

## Scope

- This phase is **engineering smoke reproduction only**.
- It does **not** claim EER, minDCF, or VoxCeleb benchmark quality.
- It does **not** run diarization, ASR, or any full speaker-verification benchmark.

## Validated Minimal Path

The validated repo-native path uses:

```python
import wespeaker

model = wespeaker.load_model("english")
model.set_device("cpu")
score = model.compute_similarity(enroll_audio, trial_audio)
```

The validated audio inputs are the tiny task-specific fixtures under `tests/fixtures/shared/speaker_verification/`. They were provisioned from existing repository SD fixtures only for engineering smoke use and are not valid for accuracy claims.

## Runtime Notes

- Backend: `pip`
- Python target: `3.10`
- Device validated: `cpu`
- Weight policy: `model_local_first`
- Model-local weight root: `checkpoints/english`
- The upstream runtime expects `avg_model.pt` and `config.yaml`; if `config.yaml` is missing but the local tarball is present, the wrapper/setup path restores it locally without fetching a dataset.

## Repro Bundle Boundaries

The repro bundle intentionally excludes:

- large checkpoints
- benchmark datasets such as VoxCeleb, CN-Celeb, and VoxConverse
- `.venv/`, `.runtime/`, and host cache directories
- the full upstream git snapshot

Only the minimal manifests, instructions, wrapper references, and tiny fixtures are included so the smoke path can be reconstructed without bundling large artifacts.
