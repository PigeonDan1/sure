# CUDA Runtime Mismatch Bad Case

## Trigger

Use this memory when import or model loading fails with CUDA, cuDNN, PyTorch,
torchvision, torchaudio, custom operator, or GPU runtime version errors.

Common evidence:

- `no kernel image is available`
- `undefined symbol`
- `operator torchvision::nms does not exist`
- `libcudart.so`, `libcudnn`, or NCCL load errors.

## Affected Step

Environment build, Docker build, Docker validation, or first model import.

## Minimum Evidence

Collect:

- exact traceback
- `python -c "import torch; print(torch.__version__, torch.version.cuda)"`
- `python -c "import torchaudio; print(torchaudio.__version__)"`
- `nvidia-smi` or host GPU evidence when available
- Docker base image tag and installed wheel indexes.

## Fix Pattern

Align the Python package set with the runtime CUDA stack. For Docker, prefer a
base image and torch wheel index that intentionally match. For conda/pixi, keep
CUDA-related dependencies in the environment file rather than relying on
ambient host packages.

Record the chosen CUDA/PyTorch pairing in the model-local spec or README.

Kimi-Audio re-onboarding example:

```text
queue: site-new-gpu
image: registry.example.com/sure/reonboard_asr_kimi_audio:v1.0
error: CUDA error: no kernel image is available for execution on the device
```

The same ASR-only validation completed on a compatible site GPU partition, and
the full ASR/S2TT/SER/SLU/GR re-onboarding run completed there as well:

```text
job_id: <job-id>
status: Completed
duration: 1m23s
```

Do not retry the same Kimi-Audio image on 5090 unless the image has been
rebuilt with a CUDA/PyTorch/bitsandbytes stack that supports that GPU
architecture.

## Verification

Before full validation, run a cheap import and device check inside the same
environment that validation will use.

```bash
python -c "import torch, torchaudio; print(torch.__version__, torch.version.cuda); print(torch.cuda.is_available())"
```
