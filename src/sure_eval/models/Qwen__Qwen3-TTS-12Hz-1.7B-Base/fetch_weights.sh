#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS_DIR="${SCRIPT_DIR}/artifacts"
LOG_PATH="${ARTIFACTS_DIR}/fetch_weights.log"
MODEL_REPO="Qwen/Qwen3-TTS-12Hz-1.7B-Base"
TOKENIZER_REPO="Qwen/Qwen3-TTS-Tokenizer-12Hz"
MODEL_DIR_LOCAL="${SCRIPT_DIR}/checkpoints/Qwen3-TTS-12Hz-1.7B-Base"
TOKENIZER_DIR_LOCAL="${SCRIPT_DIR}/checkpoints/Qwen3-TTS-Tokenizer-12Hz"
mkdir -p "${ARTIFACTS_DIR}" "${SCRIPT_DIR}/checkpoints" "${SCRIPT_DIR}/.runtime/huggingface" "${SCRIPT_DIR}/.runtime/modelscope_cache"

PYTHON_BIN="${PYTHON_BIN:-${SCRIPT_DIR}/.venv/bin/python}"
if [ ! -x "${PYTHON_BIN}" ]; then
  echo "Missing model-local Python at ${PYTHON_BIN}; run local_setup.sh first." | tee "${LOG_PATH}" >&2
  exit 1
fi

{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  source="huggingface"
  if ! HF_HOME="${SCRIPT_DIR}/.runtime/huggingface" \
    HF_HUB_CACHE="${SCRIPT_DIR}/.runtime/huggingface/hub" \
    "${PYTHON_BIN}" -m huggingface_hub.commands.huggingface_cli download "${MODEL_REPO}" \
      --local-dir "${MODEL_DIR_LOCAL}" \
      --local-dir-use-symlinks False; then
    source="modelscope_fallback"
    "${PYTHON_BIN}" -m modelscope.cli.cli download --model "${MODEL_REPO}" --local_dir "${MODEL_DIR_LOCAL}"
  fi
  if ! HF_HOME="${SCRIPT_DIR}/.runtime/huggingface" \
    HF_HUB_CACHE="${SCRIPT_DIR}/.runtime/huggingface/hub" \
    "${PYTHON_BIN}" -m huggingface_hub.commands.huggingface_cli download "${TOKENIZER_REPO}" \
      --local-dir "${TOKENIZER_DIR_LOCAL}" \
      --local-dir-use-symlinks False; then
    source="modelscope_fallback"
    "${PYTHON_BIN}" -m modelscope.cli.cli download --model "${TOKENIZER_REPO}" --local_dir "${TOKENIZER_DIR_LOCAL}"
  fi
  MODEL_DIR="${SCRIPT_DIR}" WEIGHTS_SOURCE="${source}" "${PYTHON_BIN}" - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

model_dir = Path(os.environ["MODEL_DIR"]).resolve()
model_path = model_dir / "checkpoints" / "Qwen3-TTS-12Hz-1.7B-Base"
tokenizer_path = model_dir / "checkpoints" / "Qwen3-TTS-Tokenizer-12Hz"

def file_record(path: Path) -> dict:
    return {"path": str(path.resolve()), "size_bytes": path.stat().st_size}

payload = {
    "status": "fetched" if model_path.exists() and tokenizer_path.exists() else "missing",
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "source": os.environ["WEIGHTS_SOURCE"],
    "required": True,
    "repo_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    "resolved_local_model_path": str(model_path.resolve()) if model_path.exists() else None,
    "checkpoint_root": str((model_dir / "checkpoints").resolve()),
    "hf_cache_root": str((model_dir / ".runtime" / "huggingface").resolve()),
    "modelscope_cache_root": str((model_dir / ".runtime" / "modelscope_cache").resolve()),
    "dependencies": [
        {
            "repo_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            "local_path": str(model_path.resolve()),
            "exists": model_path.exists(),
            "files": [file_record(path) for path in sorted(model_path.rglob("*")) if path.is_file()],
        },
        {
            "repo_id": "Qwen/Qwen3-TTS-Tokenizer-12Hz",
            "local_path": str(tokenizer_path.resolve()),
            "exists": tokenizer_path.exists(),
            "files": [file_record(path) for path in sorted(tokenizer_path.rglob("*")) if path.is_file()],
        },
    ],
}
(model_dir / "artifacts" / "weights_manifest.json").write_text(
    json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
    encoding="utf-8",
)
PY
} 2>&1 | tee "${LOG_PATH}"
