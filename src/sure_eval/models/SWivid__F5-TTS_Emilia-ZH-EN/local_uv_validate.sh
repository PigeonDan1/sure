#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

export UV_CACHE_DIR="${SCRIPT_DIR}/.runtime/uv-cache"
export MPLCONFIGDIR="${SCRIPT_DIR}/.runtime/matplotlib"
export DEVICE="${DEVICE:-cuda:0}"
export PYTHONPATH="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)/src:${PYTHONPATH:-}"
mkdir -p "${UV_CACHE_DIR}" "${MPLCONFIGDIR}" artifacts/outputs

if [ ! -x .venv/bin/python ]; then
  echo "Missing .venv. Run local_uv_setup.sh first." >&2
  exit 2
fi

.venv/bin/python - <<'PY'
import os
import torch
device = os.environ.get("DEVICE", "cuda:0")
print({"torch": torch.__version__, "cuda": torch.version.cuda, "cuda_available": torch.cuda.is_available(), "device": device})
if device.startswith("cuda") and not torch.cuda.is_available():
    raise SystemExit("DEVICE requests CUDA but torch.cuda.is_available() is false")
PY

.venv/bin/python validate.py 2>&1 | tee artifacts/local_uv_validate.stdout.log
