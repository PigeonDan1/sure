#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${MODEL_DIR}"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Run ./local_uv_setup.sh first." >&2
  exit 2
fi

.venv/bin/python validate.py
