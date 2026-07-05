#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

export UV_CACHE_DIR="${SCRIPT_DIR}/.runtime/uv-cache"
export UV_PYTHON_INSTALL_DIR="${SCRIPT_DIR}/.runtime/uv-python"
export MPLCONFIGDIR="${SCRIPT_DIR}/.runtime/matplotlib"
mkdir -p "${UV_CACHE_DIR}" "${UV_PYTHON_INSTALL_DIR}" "${MPLCONFIGDIR}" artifacts

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_FALLBACK:-python3.11}"
fi

uv venv --python "${PYTHON_BIN}" .venv
uv pip install --python .venv/bin/python -r requirements-local.txt
.venv/bin/python - <<'PY'
import json
from pathlib import Path
import torch

payload = {
    "status": "setup_completed",
    "torch": torch.__version__,
    "cuda": torch.version.cuda,
    "cuda_available": torch.cuda.is_available(),
    "device_count": torch.cuda.device_count(),
}
Path("artifacts/local_uv_environment.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
PY
