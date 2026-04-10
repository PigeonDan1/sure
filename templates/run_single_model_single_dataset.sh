#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_NAME="${MODEL_NAME:-my_model}"
MODEL_DIR="${MODEL_DIR:-$REPO_ROOT/src/sure_eval/models/$MODEL_NAME}"
DATASET="${DATASET:-aishell1}"
RUN_ID="${RUN_ID:-main_agent_${MODEL_NAME}_$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="${RUN_DIR:-$MODEL_DIR/eval_runs/$RUN_ID}"
TOOL_NAME="${TOOL_NAME:-transcribe_audio}"
LANGUAGE="${LANGUAGE:-}"
MAX_SAMPLES="${MAX_SAMPLES:-0}"
PYTHON_BIN="${PYTHON_BIN:-python}"
SKIP_VALIDATE_AND_EVAL="${SKIP_VALIDATE_AND_EVAL:-0}"

mkdir -p "$RUN_DIR/predictions/logs"

echo "[1/5] prepare dataset"
"$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_sure_dataset.py" \
  --dataset "$DATASET" \
  --output "$RUN_DIR/prepare_summary.json"

echo "[2/5] materialize prediction template"
"$PYTHON_BIN" "$REPO_ROOT/scripts/materialize_predictions_template.py" \
  --dataset "$DATASET" \
  --output-dir "$RUN_DIR/predictions" \
  --manifest-name manifest.json

echo "[3/5] generate predictions"
GEN_ARGS=(
  --model-dir "$MODEL_DIR"
  --dataset "$DATASET"
  --run-dir "$RUN_DIR"
  --tool-name "$TOOL_NAME"
)
if [[ -n "$LANGUAGE" ]]; then
  GEN_ARGS+=(--language "$LANGUAGE")
fi
if [[ "$MAX_SAMPLES" != "0" ]]; then
  GEN_ARGS+=(--max-samples "$MAX_SAMPLES")
fi
"$PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" "${GEN_ARGS[@]}"

if [[ "$SKIP_VALIDATE_AND_EVAL" == "1" ]]; then
  echo "Skipping validation and evaluation by request"
  echo "Run prepared through prediction generation: $RUN_DIR"
  exit 0
fi

echo "[4/5] validate predictions"
"$PYTHON_BIN" "$REPO_ROOT/scripts/validate_prediction_files.py" \
  --dataset "$DATASET" \
  --pred-dir "$RUN_DIR/predictions" \
  --require-nonempty \
  --output "$RUN_DIR/validation_payload.json"

echo "[5/5] evaluate predictions"
"$PYTHON_BIN" "$REPO_ROOT/scripts/evaluate_predictions.py" \
  --dataset "$DATASET" \
  --pred-dir "$RUN_DIR/predictions" \
  --tool-name "$MODEL_NAME" \
  --output "$RUN_DIR/evaluation_payload.json"

echo "Run completed: $RUN_DIR"
