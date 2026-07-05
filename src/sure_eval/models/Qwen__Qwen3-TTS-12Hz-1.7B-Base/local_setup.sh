#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS_DIR="${SCRIPT_DIR}/artifacts"
LOG_PATH="${ARTIFACTS_DIR}/build.log"
mkdir -p "${ARTIFACTS_DIR}" "${SCRIPT_DIR}/.runtime/uv-cache" "${SCRIPT_DIR}/.runtime/uv-python" "${SCRIPT_DIR}/.runtime/tmp"

UV_BIN="${UV_BIN:-/hpc_stor03/sjtu_home/chaolei.liu/.local/bin/uv}"
if [ ! -x "${UV_BIN}" ]; then
  UV_BIN="$(command -v uv || true)"
fi
if [ -z "${UV_BIN}" ]; then
  echo "uv is unavailable on PATH." | tee "${LOG_PATH}" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_FALLBACK:-/usr/bin/python3.11}"
fi

{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "uv_bin=${UV_BIN}"
  "${UV_BIN}" --version
  echo "python_bin=${PYTHON_BIN}"
  "${PYTHON_BIN}" --version
  UV_CACHE_DIR="${SCRIPT_DIR}/.runtime/uv-cache" \
  UV_PYTHON_INSTALL_DIR="${SCRIPT_DIR}/.runtime/uv-python" \
    "${UV_BIN}" venv --python "${PYTHON_BIN}" "${SCRIPT_DIR}/.venv"
  UV_CACHE_DIR="${SCRIPT_DIR}/.runtime/uv-cache" \
  UV_PYTHON_INSTALL_DIR="${SCRIPT_DIR}/.runtime/uv-python" \
  UV_LINK_MODE="${UV_LINK_MODE:-copy}" \
  TMPDIR="${SCRIPT_DIR}/.runtime/tmp" \
    "${UV_BIN}" pip install \
      --python "${SCRIPT_DIR}/.venv/bin/python" \
      --index-url "${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" \
      --index-strategy unsafe-best-match \
      -r "${SCRIPT_DIR}/requirements-local.txt"
  MODEL_DIR="${SCRIPT_DIR}" "${SCRIPT_DIR}/.venv/bin/python" - <<'PY'
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
import os

model_dir = Path(os.environ["MODEL_DIR"]).resolve()
artifacts = model_dir / "artifacts"
payload = {
    "status": "setup_completed",
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "venv_path": str((model_dir / ".venv").resolve()),
    "python": {
        "path": str((model_dir / ".venv/bin/python").resolve()),
        "version": platform.python_version(),
    },
    "setup_log": str((artifacts / "build.log").resolve()),
}
(artifacts / "local_env.json").write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
PY
} 2>&1 | tee "${LOG_PATH}"
