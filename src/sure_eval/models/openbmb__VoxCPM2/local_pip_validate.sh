#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [ ! -x .venv/bin/python ]; then
  echo ".venv is missing; run local_pip_setup.sh first" >&2
  exit 1
fi

export HF_HOME="${SCRIPT_DIR}/.runtime/hf-home"
export HF_HUB_CACHE="${SCRIPT_DIR}/.runtime/hf-home/hub"
export HUGGINGFACE_HUB_CACHE="${SCRIPT_DIR}/.runtime/hf-home/hub"
export TRANSFORMERS_CACHE="${SCRIPT_DIR}/.runtime/hf-home/transformers"
export MPLCONFIGDIR="${SCRIPT_DIR}/.runtime/matplotlib"
export TMPDIR="${SCRIPT_DIR}/.runtime/tmp"
mkdir -p "${HF_HOME}" "${HF_HUB_CACHE}" "${TRANSFORMERS_CACHE}" "${MPLCONFIGDIR}" "${TMPDIR}"

.venv/bin/python validate.py
