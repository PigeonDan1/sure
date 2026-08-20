#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)}"
MODEL="${MODEL:?MODEL is required}"
DATASETS="${DATASETS:?DATASETS is required}"
PIPELINE_IDS="${PIPELINE_IDS:?PIPELINE_IDS is required}"
PROTOCOL_ID="${PROTOCOL_ID:-standard_system}"
DEVICE="${DEVICE:-cpu}"
INVOCATION_RUN_DIR="${INVOCATION_RUN_DIR:?INVOCATION_RUN_DIR is required}"

if [[ "$PROTOCOL_ID" != "standard_system" && "$PROTOCOL_ID" != "strict_core" ]]; then
  echo "ERROR: PROTOCOL_ID must be standard_system or strict_core" >&2
  exit 2
fi

ARGS=(
  --model "$MODEL"
  --datasets "$DATASETS"
  --protocol-id "$PROTOCOL_ID"
  --device "$DEVICE"
  --invocation-run-dir "$INVOCATION_RUN_DIR"
)
read -r -a PIPELINE_ARRAY <<< "$PIPELINE_IDS"
for pipeline_id in "${PIPELINE_ARRAY[@]}"; do
  ARGS+=(--pipeline-id "$pipeline_id")
done

: "${HARNESS_PYTHON_BIN:?HARNESS_PYTHON_BIN must be resolved by the /sure_reval pre-start hook}"
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/sure/skills/sure_eval/scripts/run_reval.py" "${ARGS[@]}"
