#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS_DIR="${SCRIPT_DIR}/artifacts"
LOG_PATH="${ARTIFACTS_DIR}/local_uv_validate.log"
mkdir -p "${ARTIFACTS_DIR}"
cd "${SCRIPT_DIR}"

if [ ! -x .venv/bin/python ]; then
  echo ".venv is missing; run local_uv_setup.sh first" >&2
  exit 1
fi

export HF_HOME="${SCRIPT_DIR}/.runtime/hf-home"
export HF_HUB_CACHE="${SCRIPT_DIR}/.runtime/hf-home/hub"
export HUGGINGFACE_HUB_CACHE="${SCRIPT_DIR}/.runtime/hf-home/hub"
export TRANSFORMERS_CACHE="${SCRIPT_DIR}/.runtime/hf-home/transformers"
export MPLCONFIGDIR="${SCRIPT_DIR}/.runtime/matplotlib"
export TMPDIR="${SCRIPT_DIR}/.runtime/tmp"
export DEVICE="${DEVICE:-cuda:0}"
mkdir -p "${HF_HOME}" "${HF_HUB_CACHE}" "${TRANSFORMERS_CACHE}" "${MPLCONFIGDIR}" "${TMPDIR}"

{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "DEVICE=${DEVICE}"
  .venv/bin/python - <<'PY'
import torch

print(f"torch={torch.__version__}")
print(f"torch.version.cuda={torch.version.cuda}")
print(f"torch.cuda.is_available={torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"torch.cuda.device_count={torch.cuda.device_count()}")
    print(f"torch.cuda.device_name={torch.cuda.get_device_name(0)}")
PY
  .venv/bin/python validate.py
} 2>&1 | tee "${LOG_PATH}"
