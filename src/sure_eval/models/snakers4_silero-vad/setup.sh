#!/bin/bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$MODEL_DIR/.venv"
UV_BIN="${SURE_MODEL_UV_BIN:-}"
PYTHON_BIN="${SURE_MODEL_PYTHON_BIN:-}"
CACHE_DIR="$MODEL_DIR/.runtime/uv-cache"
TMP_DIR="$MODEL_DIR/.runtime/tmp"

if [ -z "$UV_BIN" ]; then
  for candidate in uv "$HOME/.local/bin/uv"; do
    if [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; then
      UV_BIN="$(command -v "$candidate" 2>/dev/null || printf '%s' "$candidate")"
      break
    fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
fi

[ -n "$UV_BIN" ] || {
  echo "uv not found. Set SURE_MODEL_UV_BIN to a usable uv binary." >&2
  exit 1
}

[ -n "$PYTHON_BIN" ] || {
  echo "No suitable Python interpreter found. Set SURE_MODEL_PYTHON_BIN." >&2
  exit 1
}

mkdir -p "$CACHE_DIR" "$TMP_DIR"

env \
  UV_CACHE_DIR="$CACHE_DIR" \
  TMPDIR="$TMP_DIR" \
  UV_PROJECT_ENVIRONMENT="$VENV_DIR" \
  "$UV_BIN" sync --project "$MODEL_DIR" --python "$PYTHON_BIN"

echo "Model-local runtime ready at $VENV_DIR/bin/python"
