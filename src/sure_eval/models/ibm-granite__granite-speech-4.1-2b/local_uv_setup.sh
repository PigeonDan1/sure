#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$MODEL_DIR"

export UV_CACHE_DIR="$MODEL_DIR/.runtime/uv-cache"
export UV_PYTHON_INSTALL_DIR="$MODEL_DIR/.runtime/uv-python"
export UV_LINK_MODE=copy
mkdir -p "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" "$MODEL_DIR/artifacts"

if [ ! -x "$MODEL_DIR/.venv/bin/python" ]; then
  uv venv --python "${PYTHON_BIN:-python3.11}" "$MODEL_DIR/.venv"
fi

uv pip install --python "$MODEL_DIR/.venv/bin/python" \
  --index-url "${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" \
  -r "$MODEL_DIR/requirements.phase1.txt"

uv pip freeze --python "$MODEL_DIR/.venv/bin/python" > "$MODEL_DIR/requirements.lock"
