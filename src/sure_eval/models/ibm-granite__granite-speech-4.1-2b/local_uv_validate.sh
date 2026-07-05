#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$MODEL_DIR"

export HF_HOME="$MODEL_DIR/.runtime/hf-home"
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export MPLCONFIGDIR="$MODEL_DIR/.runtime/matplotlib"
export PYTHONPATH="$MODEL_DIR:${PYTHONPATH:-}"
export DEVICE="${DEVICE:-auto}"
mkdir -p "$HF_HOME" "$HUGGINGFACE_HUB_CACHE" "$MPLCONFIGDIR" "$MODEL_DIR/artifacts"

"$MODEL_DIR/.venv/bin/python" "$MODEL_DIR/validate.py" 2>&1 | tee "$MODEL_DIR/artifacts/local_uv_validate.stdout.log"
