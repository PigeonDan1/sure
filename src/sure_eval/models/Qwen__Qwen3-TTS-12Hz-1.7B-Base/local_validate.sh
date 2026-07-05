#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_PATH="${SCRIPT_DIR}/artifacts/local_validate.log"
mkdir -p "${SCRIPT_DIR}/artifacts"

PYTHON_BIN="${PYTHON_BIN:-${SCRIPT_DIR}/.venv/bin/python}"
if [ ! -x "${PYTHON_BIN}" ]; then
  echo "Missing model-local Python at ${PYTHON_BIN}; run local_setup.sh first." | tee "${LOG_PATH}" >&2
  exit 1
fi

cd "${SCRIPT_DIR}"
HF_HOME="${SCRIPT_DIR}/.runtime/huggingface" \
HF_HUB_CACHE="${SCRIPT_DIR}/.runtime/huggingface/hub" \
MODELSCOPE_CACHE="${SCRIPT_DIR}/.runtime/modelscope_cache" \
MPLCONFIGDIR="${SCRIPT_DIR}/.runtime/matplotlib" \
TMPDIR="${SCRIPT_DIR}/.runtime/tmp" \
"${PYTHON_BIN}" validate.py 2>&1 | tee "${LOG_PATH}"
