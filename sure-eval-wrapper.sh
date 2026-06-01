#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="$SCRIPT_DIR/.venv/bin:$PATH"
exec python -m sure_eval.cli "$@"
