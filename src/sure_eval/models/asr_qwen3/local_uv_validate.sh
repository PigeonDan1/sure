#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.runtime/uv-cache}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$PWD/.runtime/matplotlib}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$PWD/.runtime/modelscope_cache}"
export MODEL_PATH="${MODEL_PATH:-$PWD/.runtime/modelscope_cache/models/Qwen/Qwen3-ASR-1___7B}"
export DEVICE="${DEVICE:-cuda}"
export REQUIRE_GPU="${REQUIRE_GPU:-1}"
mkdir -p "$MPLCONFIGDIR" artifacts

.venv/bin/python validate.py 2>&1 | tee artifacts/local_uv_validate.stdout.log
