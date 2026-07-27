#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)}"
SOURCE="${SOURCE:?SOURCE is required}"
DATASETS="${DATASETS:-}"
METRICS="${METRICS:-}"
PIPELINE_IDS="${PIPELINE_IDS:-}"
MAX_SAMPLES="${MAX_SAMPLES:-0}"
DEVICE="${DEVICE:-cpu}"
OUTPUT_DIR="${OUTPUT_DIR:-}"

ARGS=(--source "$SOURCE" --max-samples "$MAX_SAMPLES" --device "$DEVICE")
if [[ -n "$DATASETS" ]]; then
  ARGS+=(--datasets "$DATASETS")
fi
if [[ -n "$METRICS" ]]; then
  ARGS+=(--metrics "$METRICS")
fi
if [[ -n "$PIPELINE_IDS" ]]; then
  read -r -a PIPELINE_ARRAY <<< "$PIPELINE_IDS"
  for pipeline_id in "${PIPELINE_ARRAY[@]}"; do
    ARGS+=(--pipeline-id "$pipeline_id")
  done
fi
if [[ -n "$OUTPUT_DIR" ]]; then
  ARGS+=(--output-dir "$OUTPUT_DIR")
fi

python3 "$REPO_ROOT/sure/skills/sure_eval/scripts/run_reval.py" "${ARGS[@]}"
