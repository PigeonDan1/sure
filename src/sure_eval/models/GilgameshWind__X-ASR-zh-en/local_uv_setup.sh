#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS_DIR="${SCRIPT_DIR}/artifacts"
mkdir -p "${ARTIFACTS_DIR}"
LOG_PATH="${ARTIFACTS_DIR}/local_uv_setup.log"
export UV_CACHE_DIR="${SCRIPT_DIR}/.runtime/uv-cache"
mkdir -p "${UV_CACHE_DIR}"

cd "${SCRIPT_DIR}"

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required for local tool-agent bootstrap" | tee "${LOG_PATH}" >&2
  exit 1
fi

{
  echo "PYTHON_BIN=${PYTHON_BIN}"
  uv --version
  uv venv --python "${PYTHON_BIN}" .venv
  uv pip install --python .venv/bin/python --index-url "${UV_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" -r requirements-core.txt
  .venv/bin/python --version
} 2>&1 | tee "${LOG_PATH}"
