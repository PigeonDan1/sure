#!/bin/bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_PYTHON="/opt/anaconda3/envs/fireredvad/bin/python3.10"
PYTHON_BIN="${SURE_WESPEAKER_PYTHON_BIN:-$DEFAULT_PYTHON}"
VENV_DIR="${SURE_WESPEAKER_VENV_DIR:-$MODEL_DIR/.venv}"
PIP_CACHE_DIR="${SURE_WESPEAKER_PIP_CACHE_DIR:-$MODEL_DIR/.runtime/pip-cache}"

if [ ! -x "$PYTHON_BIN" ]; then
  if command -v python3.10 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.10)"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    echo "No suitable Python interpreter found. Set SURE_WESPEAKER_PYTHON_BIN." >&2
    exit 1
  fi
fi

mkdir -p "$PIP_CACHE_DIR"

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

PIP_BIN="$VENV_DIR/bin/pip"
PY_BIN="$VENV_DIR/bin/python"

"$PIP_BIN" install --cache-dir "$PIP_CACHE_DIR" "setuptools<81" wheel
"$PIP_BIN" install --cache-dir "$PIP_CACHE_DIR" \
  requests==2.32.3 \
  numpy==1.26.4 \
  PyYAML==6.0.2 \
  kaldiio==2.18.1 \
  tqdm==4.67.1 \
  silero-vad==6.2.0 \
  soundfile==0.13.1 \
  torch==2.1.2 \
  torchaudio==2.1.2
"$PIP_BIN" install --no-build-isolation --no-deps -e "$MODEL_DIR/upstream"

"$PY_BIN" - "$MODEL_DIR" <<'PY'
import tarfile
import sys
from pathlib import Path

model_dir = Path(sys.argv[1]) / "checkpoints" / "english"
archive_path = model_dir / "voxceleb_resnet221_LM.tar.gz"
config_path = model_dir / "config.yaml"
if archive_path.exists() and not config_path.exists():
    with tarfile.open(archive_path, "r:gz") as archive:
        member = archive.getmember("voxceleb_resnet221_LM/config.yaml")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise RuntimeError("Failed to extract config.yaml from local WeSpeaker archive")
        config_path.write_bytes(extracted.read())
PY

echo "WeSpeaker setup complete."
echo "Python: $PYTHON_BIN"
echo "Virtualenv: $VENV_DIR"
echo "Use: $VENV_DIR/bin/python $MODEL_DIR/validate_phase1.py"
