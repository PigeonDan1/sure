#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"
PREFIX="${CONDA_PREFIX_PATH:-${SCRIPT_DIR}/.runtime/conda/sure-fun-cosyvoice3-0-5b-2512}"
LOG_PATH="${SCRIPT_DIR}/artifacts/local_conda_validate.log"

if [ ! -x "${PREFIX}/bin/python" ]; then
  echo "Missing model-local conda Python: ${PREFIX}/bin/python" | tee "${LOG_PATH}" >&2
  exit 1
fi

mkdir -p "${SCRIPT_DIR}/artifacts"

{
  date -u +%FT%TZ
  "${PREFIX}/bin/python" --version
  PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}" \
  HF_HOME="${SCRIPT_DIR}/.runtime/huggingface" \
  HF_HUB_CACHE="${SCRIPT_DIR}/.runtime/huggingface/hub" \
  MODELSCOPE_CACHE="${SCRIPT_DIR}/.runtime/modelscope_cache" \
  DEVICE="${DEVICE:-cuda:0}" \
    "${PREFIX}/bin/python" "${SCRIPT_DIR}/validate.py"
} 2>&1 | tee "${LOG_PATH}"
