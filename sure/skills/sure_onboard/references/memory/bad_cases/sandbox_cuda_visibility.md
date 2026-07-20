# Bad Case: Sandbox CUDA Visibility Is Not GPU Evidence

## Trigger

Read this file only when these symptoms appear together:

- `nvidia-smi` on the host sees one or more GPUs; and
- a sandboxed Python command reports one of:
  - `torch.cuda.is_available() == False`
  - `torch.cuda.device_count() == 0`
  - `UserWarning: Can't initialize NVML`
  - model inference fails with `RuntimeError: No CUDA GPUs are available`

## Affected Step

`VALIDATE_INFER` / GPU preflight for local `uv`, Docker, or model-local Python
validation.

## Minimum Evidence To Collect

Run all three checks and record them in `gpu_status.json` or
`failure_classification.json`:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy nvidia-smi
```

```bash
python - <<'PY'
import os, torch
print("CUDA_VISIBLE_DEVICES=", os.environ.get("CUDA_VISIBLE_DEVICES"))
print("torch.version.cuda=", torch.version.cuda)
print("torch.cuda.is_available=", torch.cuda.is_available())
print("torch.cuda.device_count=", torch.cuda.device_count())
PY
```

Then rerun the same Python self-check outside the sandbox with proxy variables
cleared:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  <model-venv>/bin/python - <<'PY'
import os, torch
print("CUDA_VISIBLE_DEVICES=", os.environ.get("CUDA_VISIBLE_DEVICES"))
print("torch.version.cuda=", torch.version.cuda)
print("torch.cuda.is_available=", torch.cuda.is_available())
print("torch.cuda.device_count=", torch.cuda.device_count())
if torch.cuda.is_available():
    print("device0=", torch.cuda.get_device_name(0))
PY
```

## Known Cause

The Codex sandbox can hide or block CUDA/NVML device discovery. In that case,
`torch.cuda.is_available() == False` inside the sandbox does not prove the
model-local uv environment or host GPU is broken.

## Required Fix

- Do not accept sandbox CPU fallback as final GPU evidence.
- Rerun GPU validation outside the sandbox with proxy variables cleared.
- If non-sandbox self-check sees GPUs, run the model validate command outside
  the sandbox.
- If non-sandbox self-check also fails, then classify as CUDA/runtime mismatch
  and route to `cuda_runtime_mismatch.md`.

## Verification Command

Use the model's actual validation command outside the sandbox, for example:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  CUDA_VISIBLE_DEVICES=0 ./local_uv_validate.sh
```

## Example

`src/sure_eval/models_reonboard/runs/asr_fireredasr`:

- sandboxed `validate.py` failed with `RuntimeError: No CUDA GPUs are available`;
- host `nvidia-smi` saw four RTX 2080 Ti GPUs;
- the same `.venv` outside the sandbox reported `torch.cuda.is_available=True`
  and `torch.cuda.device_count=4`.

The correct next step is non-sandbox GPU validation, not CPU fallback.
