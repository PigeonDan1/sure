#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS_DIR="${SCRIPT_DIR}/artifacts"
LOG_PATH="${ARTIFACTS_DIR}/build.log"
mkdir -p "${ARTIFACTS_DIR}" "${SCRIPT_DIR}/.runtime/uv-cache" "${SCRIPT_DIR}/.runtime/uv-python"

export UV_CACHE_DIR="${SCRIPT_DIR}/.runtime/uv-cache"
export UV_PYTHON_INSTALL_DIR="${SCRIPT_DIR}/.runtime/uv-python"
export UV_LINK_MODE=copy

UV_BIN="${UV_BIN:-${SCRIPT_DIR}/.runtime/bootstrap/bin/uv}"
PYTHON_VERSION="${PYTHON_VERSION:-3.10}"

cd "${SCRIPT_DIR}"
{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "UV_BIN=${UV_BIN}"
  "${UV_BIN}" --version
  "${UV_BIN}" venv --python "${PYTHON_VERSION}" .venv
  .venv/bin/python --version
  if [ ! -d ".runtime/source/dots.tts" ]; then
    echo "Missing upstream source directory: .runtime/source/dots.tts"
    exit 32
  fi
  "${UV_BIN}" pip install --python .venv/bin/python --index-strategy unsafe-best-match -r requirements-local.txt
  "${UV_BIN}" pip freeze --python .venv/bin/python > requirements.lock
} 2>&1 | tee "${LOG_PATH}"
