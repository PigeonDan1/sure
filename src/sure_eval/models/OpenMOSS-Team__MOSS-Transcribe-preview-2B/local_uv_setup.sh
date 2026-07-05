#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

mkdir -p .runtime/uv-cache .runtime/uv-python .runtime/hf-home/hub .runtime/tmp .runtime/matplotlib artifacts

export UV_CACHE_DIR="${SCRIPT_DIR}/.runtime/uv-cache"
export UV_PYTHON_INSTALL_DIR="${SCRIPT_DIR}/.runtime/uv-python"
export UV_LINK_MODE=copy
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"
export TMPDIR="${SCRIPT_DIR}/.runtime/tmp"

{
  echo "BUILD_ENV started at $(date -Is)"
  echo "uv: $(uv --version)"
  uv venv .venv --python 3.10
  uv pip install --python .venv/bin/python -r requirements.txt
  .venv/bin/python --version
  .venv/bin/python - <<'PY'
import torch
import transformers
import huggingface_hub
import librosa
print("torch", torch.__version__, "cuda_available", torch.cuda.is_available(), "cuda_devices", torch.cuda.device_count())
print("transformers", transformers.__version__)
print("huggingface_hub", huggingface_hub.__version__)
print("librosa", librosa.__version__)
PY
  uv pip freeze --python .venv/bin/python > requirements.lock
  echo "BUILD_ENV finished at $(date -Is)"
} 2>&1 | tee artifacts/build.log
