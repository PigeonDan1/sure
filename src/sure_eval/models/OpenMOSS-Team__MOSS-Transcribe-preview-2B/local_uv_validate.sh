#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

export HF_HOME="${SCRIPT_DIR}/.runtime/hf-home"
export HF_HUB_CACHE="${SCRIPT_DIR}/.runtime/hf-home/hub"
export TRANSFORMERS_CACHE="${HF_HUB_CACHE}"
export MPLCONFIGDIR="${SCRIPT_DIR}/.runtime/matplotlib"
export TMPDIR="${SCRIPT_DIR}/.runtime/tmp"
export DEVICE="${DEVICE:-cuda:0}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

mkdir -p "${HF_HOME}" "${HF_HUB_CACHE}" "${MPLCONFIGDIR}" "${TMPDIR}" artifacts

.venv/bin/python validate_runtime.py --stage all
.venv/bin/python validate.py
