#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS_DIR="${SCRIPT_DIR}/artifacts"
LOG_PATH="${ARTIFACTS_DIR}/build.log"
mkdir -p \
  "${ARTIFACTS_DIR}" \
  "${SCRIPT_DIR}/.runtime/uv-cache" \
  "${SCRIPT_DIR}/.runtime/uv-python" \
  "${SCRIPT_DIR}/.runtime/tmp" \
  "${SCRIPT_DIR}/.runtime/hf-home" \
  "${SCRIPT_DIR}/.runtime/matplotlib"

cd "${SCRIPT_DIR}"

UV_BIN="${UV_BIN:-uv}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"

if ! command -v "${UV_BIN}" >/dev/null 2>&1; then
  echo "uv is required for the selected phase-1 backend but was not found on PATH" >&2
  exit 1
fi

export UV_CACHE_DIR="${SCRIPT_DIR}/.runtime/uv-cache"
export UV_PYTHON_INSTALL_DIR="${SCRIPT_DIR}/.runtime/uv-python"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}"
export TMPDIR="${SCRIPT_DIR}/.runtime/tmp"

{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "UV_BIN=$(command -v "${UV_BIN}")"
  "${UV_BIN}" --version
  echo "PYTHON_BIN=${PYTHON_BIN}"
  "${PYTHON_BIN}" --version

  rm -rf .venv
  "${UV_BIN}" venv --python "${PYTHON_BIN}" .venv

  "${UV_BIN}" pip install \
    --python .venv/bin/python \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements-local.txt

  "${UV_BIN}" pip install \
    --python .venv/bin/python \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements-minimal.txt

  "${UV_BIN}" pip install \
    --python .venv/bin/python \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --no-deps \
    voxcpm==2.0.3

  "${UV_BIN}" pip freeze --python .venv/bin/python > requirements.lock

  .venv/bin/python - <<'PY'
import importlib.metadata as md
import torch

print(f"python runtime ok")
print(f"torch=={torch.__version__}")
print(f"torch.version.cuda=={torch.version.cuda}")
print(f"torch.cuda.is_available=={torch.cuda.is_available()}")
for name in ("torchaudio", "voxcpm", "transformers", "huggingface-hub", "modelscope"):
    try:
        print(f"{name}=={md.version(name)}")
    except md.PackageNotFoundError:
        print(f"{name}=MISSING")
PY
} 2>&1 | tee "${LOG_PATH}"
