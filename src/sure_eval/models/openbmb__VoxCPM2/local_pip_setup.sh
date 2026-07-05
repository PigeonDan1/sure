#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS_DIR="${SCRIPT_DIR}/artifacts"
LOG_PATH="${ARTIFACTS_DIR}/build.log"
mkdir -p "${ARTIFACTS_DIR}" "${SCRIPT_DIR}/.runtime/pip-cache" "${SCRIPT_DIR}/.runtime/tmp"

cd "${SCRIPT_DIR}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_FALLBACK:-python3}"
fi

{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "PYTHON_BIN=${PYTHON_BIN}"
  "${PYTHON_BIN}" --version
  rm -rf .venv
  "${PYTHON_BIN}" -m venv .venv
  PIP_CACHE_DIR="${SCRIPT_DIR}/.runtime/pip-cache" \
  TMPDIR="${SCRIPT_DIR}/.runtime/tmp" \
    .venv/bin/python -m pip install \
      --timeout 120 \
      --retries 5 \
      --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
      -r requirements-local.txt
  PIP_CACHE_DIR="${SCRIPT_DIR}/.runtime/pip-cache" \
  TMPDIR="${SCRIPT_DIR}/.runtime/tmp" \
    .venv/bin/python -m pip install \
      --timeout 120 \
      --retries 5 \
      --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
      -r requirements-minimal.txt
  PIP_CACHE_DIR="${SCRIPT_DIR}/.runtime/pip-cache" \
  TMPDIR="${SCRIPT_DIR}/.runtime/tmp" \
    .venv/bin/python -m pip install \
      --timeout 120 \
      --retries 5 \
      --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
      --no-deps \
      voxcpm==2.0.3
  .venv/bin/python -m pip freeze > requirements.lock
  .venv/bin/python --version
  .venv/bin/python - <<'PY'
import importlib.metadata as md
for name in ("torch", "torchaudio", "voxcpm", "transformers", "huggingface-hub"):
    try:
        print(f"{name}=={md.version(name)}")
    except md.PackageNotFoundError:
        print(f"{name}=MISSING")
PY
} 2>&1 | tee "${LOG_PATH}"
