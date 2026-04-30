#!/bin/bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$MODEL_DIR/.venv"

pick_python() {
  if [ -n "${SURE_MODEL_PYTHON_BIN:-}" ] && [ -x "${SURE_MODEL_PYTHON_BIN}" ]; then
    printf '%s\n' "${SURE_MODEL_PYTHON_BIN}"
    return 0
  fi

  for candidate in python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done

  return 1
}

PYTHON_BIN="$(pick_python)" || {
  echo "No suitable Python interpreter found. Set SURE_MODEL_PYTHON_BIN." >&2
  exit 1
}

if [ -L "$VENV_DIR" ]; then
  rm -f "$VENV_DIR"
fi

if [ -d "$VENV_DIR" ] && [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv --clear "$VENV_DIR"
elif [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

echo "Model-local runtime ready at $VENV_DIR/bin/python"
