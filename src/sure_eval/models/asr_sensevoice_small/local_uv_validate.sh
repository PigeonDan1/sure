#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.runtime/uv-cache}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$PWD/.runtime/matplotlib}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$PWD/.runtime/modelscope_cache}"
export SENSEVOICE_MODEL_PATH="${SENSEVOICE_MODEL_PATH:-$PWD/.runtime/modelscope_cache/models/iic/SenseVoiceSmall}"
export DEVICE="${DEVICE:-auto}"
mkdir -p "$MPLCONFIGDIR" artifacts

.venv/bin/python validate.py 2>&1 | tee artifacts/local_uv_validate.stdout.log
