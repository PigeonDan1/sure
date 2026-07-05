#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel)"
TARGET_DIR="${REPO_ROOT}/tests/fixtures/shared/tts"
SOURCE_AUDIO="${SCRIPT_DIR}/fixture/tts/en/voice_clone_ref_en.wav"

mkdir -p "${TARGET_DIR}"
cp "${SOURCE_AUDIO}" "${TARGET_DIR}/en_ref.wav"

python3 - <<PY
from pathlib import Path

path = Path("${TARGET_DIR}") / "en_ref.wav"
if not path.exists() or path.stat().st_size <= 0:
    raise SystemExit(f"fixture materialization failed for {path}")
print(f"materialized requested TTS fixture at {path}")
PY
