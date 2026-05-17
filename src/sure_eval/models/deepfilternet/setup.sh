#!/bin/bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$MODEL_DIR/.venv"

pick_python() {
  if [ -n "${SURE_MODEL_PYTHON_BIN:-}" ] && [ -x "${SURE_MODEL_PYTHON_BIN}" ]; then
    printf '%s\n' "${SURE_MODEL_PYTHON_BIN}"
    return 0
  fi

  if [ -x "$VENV_DIR/bin/python" ]; then
    printf '%s\n' "$VENV_DIR/bin/python"
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

PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ "$PYTHON_VERSION" != "3.10" ]; then
  if ! command -v cargo >/dev/null 2>&1 || ! command -v rustc >/dev/null 2>&1; then
    cat >&2 <<EOF
DeepFilterNet setup requires either:
  - python3.10, so pip can use compatible prebuilt deepfilterlib wheels, or
  - Rust/Cargo, so deepfilterlib can be built for Python $PYTHON_VERSION.

Current interpreter: $PYTHON_BIN (Python $PYTHON_VERSION)
Missing system dependency: python3.10 or cargo+rustc.
Install one of those system dependencies, then rerun this setup script.
EOF
    exit 1
  fi
fi

if [ -L "$VENV_DIR" ]; then
  rm -f "$VENV_DIR"
fi

if [ -d "$VENV_DIR" ] && [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv --clear "$VENV_DIR"
elif [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
  ENSUREPIP_LOG="$(mktemp "${TMPDIR:-/tmp}/sure-deepfilternet-ensurepip.XXXXXX.log")"
  if ! "$VENV_DIR/bin/python" -m ensurepip --upgrade --default-pip >"$ENSUREPIP_LOG" 2>&1; then
    cat >&2 <<EOF
DeepFilterNet setup could not bootstrap pip in the model-local venv.

Current interpreter: $VENV_DIR/bin/python (Python $PYTHON_VERSION)
Missing system dependency: working python3.10 ensurepip/pyexpat runtime.
If this is Homebrew python3.10, install or relink its expat dependency, then rerun this setup script.

ensurepip log:
EOF
    cat "$ENSUREPIP_LOG" >&2
    exit 1
  fi
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e "$MODEL_DIR"

echo "Model-local runtime ready at $VENV_DIR/bin/python"
