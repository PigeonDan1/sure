#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${SCRIPT_DIR}/artifacts}"
LOG_PATH="${ARTIFACTS_DIR}/local_uv_validate.log"
mkdir -p "${ARTIFACTS_DIR}"

export HF_HOME="${SCRIPT_DIR}/.runtime/hf-home"
export HF_HUB_CACHE="${SCRIPT_DIR}/.runtime/hf-home/hub"
export MPLCONFIGDIR="${SCRIPT_DIR}/.runtime/matplotlib"
export XDG_CACHE_HOME="${SCRIPT_DIR}/.runtime/xdg-cache"
export PYTHONPATH="${SCRIPT_DIR}:${SCRIPT_DIR}/.runtime/source/dots.tts/src${PYTHONPATH:+:${PYTHONPATH}}"
export DEVICE="${DEVICE:-cuda:0}"
export PRECISION="${PRECISION:-float16}"
export MAX_GENERATE_LENGTH="${MAX_GENERATE_LENGTH:-900}"

cd "${SCRIPT_DIR}"
{
  .venv/bin/python - <<'PY'
import torch
print({"torch": torch.__version__, "cuda": torch.version.cuda, "available": torch.cuda.is_available(), "count": torch.cuda.device_count()})
PY
  .venv/bin/python validate.py
} 2>&1 | tee "${LOG_PATH}"
