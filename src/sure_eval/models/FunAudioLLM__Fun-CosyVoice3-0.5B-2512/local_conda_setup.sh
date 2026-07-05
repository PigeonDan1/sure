#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/.runtime/source/CosyVoice"
PREFIX="${CONDA_PREFIX_PATH:-${SCRIPT_DIR}/.runtime/conda/sure-fun-cosyvoice3-0-5b-2512}"
PIP_CACHE_DIR="${PIP_CACHE_DIR:-${SCRIPT_DIR}/.runtime/pip-cache}"
LOG_PATH="${SCRIPT_DIR}/artifacts/build.log"

mkdir -p "$(dirname "${PREFIX}")" "${PIP_CACHE_DIR}" "${SCRIPT_DIR}/artifacts"

{
  date -u +%FT%TZ
  echo "PREFIX=${PREFIX}"
  echo "SOURCE_DIR=${SOURCE_DIR}"
  if [ ! -x "${PREFIX}/bin/python" ]; then
    conda create -y -p "${PREFIX}" python=3.10 pip
  fi
  "${PREFIX}/bin/python" --version
  "${PREFIX}/bin/python" -m pip install --upgrade pip setuptools wheel
  "${PREFIX}/bin/python" -m pip install --cache-dir "${PIP_CACHE_DIR}" -r "${SCRIPT_DIR}/requirements-phase1.txt"
  "${PREFIX}/bin/python" -m pip install --cache-dir "${PIP_CACHE_DIR}" --no-deps -e "${SOURCE_DIR}/third_party/Matcha-TTS"
  "${PREFIX}/bin/python" -m pip freeze > "${SCRIPT_DIR}/artifacts/pip_freeze.txt"
} 2>&1 | tee "${LOG_PATH}"
