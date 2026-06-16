#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${MODEL_DIR}"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${MODEL_DIR}/../../../../.venv.hostbak/bin/python" ]]; then
    PYTHON_BIN="${MODEL_DIR}/../../../../.venv.hostbak/bin/python"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi
UV_CACHE_DIR="${UV_CACHE_DIR:-${MODEL_DIR}/.runtime/uv-cache}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export UV_CACHE_DIR

uv venv --python "${PYTHON_BIN}" .venv
uv pip install --python .venv/bin/python --index-url "${PIP_INDEX_URL}" -r requirements-uv.txt

{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "python=$(.venv/bin/python --version)"
  echo "uv=$(uv --version)"
  .venv/bin/python - <<'PY'
import torch
import torchaudio
print(f"torch={torch.__version__}")
print(f"torchaudio={torchaudio.__version__}")
print(f"cuda_available={torch.cuda.is_available()}")
PY
} > artifacts/build.log
