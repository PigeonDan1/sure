#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"
ARTIFACTS_DIR="${SCRIPT_DIR}/artifacts"
mkdir -p "${ARTIFACTS_DIR}"
LOG_PATH="${ARTIFACTS_DIR}/local_uv_validate.log"

cd "${SCRIPT_DIR}"
if [ ! -x ".venv/bin/python" ]; then
  echo "Missing model-local .venv. Run local_uv_setup.sh first." | tee "${LOG_PATH}" >&2
  exit 1
fi

{
  .venv/bin/python --version
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
    HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}" \
    CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" \
    DEVICE="${DEVICE:-cuda}" \
    DIFFUSION_STEPS="${DIFFUSION_STEPS:-2}" \
    PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}" \
    SURE_XFORGE_STATIC_ONLY="${SURE_XFORGE_STATIC_ONLY:-0}" \
    .venv/bin/python validate.py
} 2>&1 | tee "${LOG_PATH}"

.venv/bin/python - <<'PY'
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

model_dir = Path.cwd()
artifacts = model_dir / "artifacts"
payload = {
    "status": "passed",
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "venv_path": str((model_dir / ".venv").resolve()),
    "python": {
        "path": str((model_dir / ".venv" / "bin" / "python").resolve()),
        "version": platform.python_version(),
    },
    "validation_log": str((artifacts / "local_uv_validate.log").resolve()),
}
(artifacts / "local_uv_validation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
