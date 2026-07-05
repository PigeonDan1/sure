#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.runtime/uv-cache}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$PWD/.runtime/matplotlib}"
export WHISPER_DOWNLOAD_ROOT="${WHISPER_DOWNLOAD_ROOT:-$PWD/checkpoints}"
export MODEL_ID="${MODEL_ID:-turbo}"
export DEVICE="${DEVICE:-auto}"
mkdir -p "$MPLCONFIGDIR" artifacts

.venv/bin/python validate.py 2>&1 | tee artifacts/local_uv_validate.stdout.log
