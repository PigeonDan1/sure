#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${MODEL_DIR}"

mkdir -p artifacts .runtime/hf-home .runtime/nemo-cache .runtime/tmp .runtime/matplotlib
export HF_HOME="${MODEL_DIR}/.runtime/hf-home"
export HUGGINGFACE_HUB_CACHE="${HF_HOME}/hub"
export NEMO_CACHE_DIR="${MODEL_DIR}/.runtime/nemo-cache"
export XDG_CACHE_HOME="${MODEL_DIR}/.runtime/xdg-cache"
export TMPDIR="${MODEL_DIR}/.runtime/tmp"
export MPLCONFIGDIR="${MODEL_DIR}/.runtime/matplotlib"
export DEVICE="${DEVICE:-cpu}"

LOG="${MODEL_DIR}/artifacts/local_uv_validate.log"
: > "${LOG}"
{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "model_dir=${MODEL_DIR}"
  echo "device=${DEVICE}"
  .runtime/.venv/bin/python fetch_weights.py
  .runtime/.venv/bin/python validate.py
} 2>&1 | tee -a "${LOG}"
