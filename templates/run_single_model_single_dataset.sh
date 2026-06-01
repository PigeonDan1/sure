#!/usr/bin/env bash
set -uo pipefail

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
RESULTS_DIR="${RESULTS_DIR:-$REPO_ROOT/results/$MODEL_NAME/strict_core}"
PROTOCOL_ID="${PROTOCOL_ID:-strict_core}"

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

# [2.5/5] Smoke test gate
SMOKE_TEST_SAMPLES="${SMOKE_TEST_SAMPLES:-10}"
echo "[2.5/5] smoke test (${SMOKE_TEST_SAMPLES} samples)..."
"$PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" \
  --model-dir "$MODEL_DIR" \
  --dataset "$DATASET" \
  --run-dir "$RUN_DIR" \
  --tool-name "$TOOL_NAME" \
  --max-samples "$SMOKE_TEST_SAMPLES" \
  --resume

# Validate smoke test results
SMOKE_PRED="$RUN_DIR/predictions/${DATASET}.txt"
SMOKE_OK=0
if [[ -f "$SMOKE_PRED" ]]; then
  SMOKE_LINES=$(wc -l < "$SMOKE_PRED" || echo 0)
  SMOKE_NONEMPTY=$(awk -F'\t' '$2!="" {print}' "$SMOKE_PRED" | wc -l || echo 0)
  if [[ "$SMOKE_NONEMPTY" -ge 1 ]]; then
    SMOKE_OK=1
  fi
fi

if [[ "$SMOKE_OK" != "1" ]]; then
  echo "ERROR: Smoke test failed. No valid predictions in first ${SMOKE_TEST_SAMPLES} samples."
  echo "Check: $RUN_DIR/predictions/logs/${DATASET}_results.log"
  exit 1
fi

echo "Smoke test passed (${SMOKE_NONEMPTY}/${SMOKE_LINES} valid). Proceeding to full run..."

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
if [[ "${NO_RESUME:-0}" != "1" ]]; then
  GEN_ARGS+=(--resume)
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
EVAL_EXIT=0
"$PYTHON_BIN" "$REPO_ROOT/scripts/evaluate_predictions.py" \
  --dataset "$DATASET" \
  --pred-dir "$RUN_DIR/predictions" \
  --tool-name "$MODEL_NAME" \
  --results-dir "$RESULTS_DIR" \
  --protocol-id "$PROTOCOL_ID" \
  --model-dir "$MODEL_DIR" \
  --output "$RUN_DIR/evaluation_payload.json" || EVAL_EXIT=$?

if [[ "$EVAL_EXIT" != "0" ]]; then
  echo "WARNING: Evaluation exited with code $EVAL_EXIT"
  echo "Run directory: $RUN_DIR"
  echo "Check predictions and logs before deciding next step."
fi

echo "Run completed (with possible warnings): $RUN_DIR"
