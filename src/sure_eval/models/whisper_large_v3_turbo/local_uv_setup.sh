#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.runtime/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/.runtime/uv-python}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$PWD/.runtime/matplotlib}"
UV_INDEX_URL="${UV_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
mkdir -p "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" "$MPLCONFIGDIR" artifacts

uv venv --python "${PYTHON_BIN:-/usr/bin/python3.11}" .venv
uv pip install --index-url "$UV_INDEX_URL" --python .venv/bin/python \
  "torch==2.4.0" openai-whisper imageio-ffmpeg

.venv/bin/python - <<'PY'
import torch, whisper
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("whisper", getattr(whisper, "__version__", "unknown"))
PY
