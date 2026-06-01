#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox"
MODEL_NAME="asr_fireredasr"
RUN_ID="main_agent_asr_fireredasr_001"
RUN_DIR="src/sure_eval/models/asr_fireredasr/eval_runs/main_agent_asr_fireredasr_001"
TOOL_NAME="asr_transcribe"
PYTHON_BIN="/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/.venv/bin/python"
MAX_SAMPLES="${MAX_SAMPLES:-0}"
SKIP_VALIDATE_AND_EVAL="${SKIP_VALIDATE_AND_EVAL:-0}"
NO_RESUME="${NO_RESUME:-0}"
LANGUAGE="${LANGUAGE:-}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$MODEL_DIR/.runtime/hf_cache}"
RESULTS_DIR="${RESULTS_DIR:-$REPO_ROOT/results/$MODEL_NAME/strict_core}"
PROTOCOL_ID="${PROTOCOL_ID:-strict_core}"
export SURE_EVAL_CONFIG="$RUN_DIR/custom_config.yaml"

MODEL_DIR="$REPO_ROOT/src/sure_eval/models/$MODEL_NAME"
mkdir -p "$RUN_DIR/predictions/logs"

echo "========================================"
echo "SURE-EVAL Run: $RUN_ID"
echo "Model: $MODEL_NAME"
echo "Datasets: cs_dialogue kespeech voxpopuli aishell5 contextasr_en contextasr_zh"
echo "MAX_SAMPLES: $MAX_SAMPLES"
echo "SKIP_VALIDATE_AND_EVAL: $SKIP_VALIDATE_AND_EVAL"
echo "========================================"

# [1/5] Prepare datasets
echo "[1/5] Preparing datasets..."
"$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_sure_dataset.py" \
  --config "$RUN_DIR/custom_config.yaml" \
  --dataset cs_dialogue kespeech voxpopuli aishell5 contextasr_en contextasr_zh \
  --output "$RUN_DIR/prepare_summary.json"

# [2/5] Materialize prediction templates
echo "[2/5] Materializing prediction templates..."
"$PYTHON_BIN" "$REPO_ROOT/scripts/materialize_predictions_template.py" \
  --config "$RUN_DIR/custom_config.yaml" \
  --dataset cs_dialogue kespeech voxpopuli aishell5 contextasr_en contextasr_zh \
  --output-dir "$RUN_DIR/predictions" \
  --manifest-name manifest.json \
  --overwrite

# [2.5/5] Smoke test gate
echo "[2.5/5] Smoke test..."
SMOKE_TEST_DATASET="cs_dialogue"
SMOKE_TEST_SAMPLES="${SMOKE_TEST_SAMPLES:-10}"
SMOKE_ARGS=(
  --config "$RUN_DIR/custom_config.yaml"
  --model-dir "$MODEL_DIR"
  --dataset "$SMOKE_TEST_DATASET"
  --run-dir "$RUN_DIR"
  --tool-name "$TOOL_NAME"
  --max-samples "$SMOKE_TEST_SAMPLES"
)
if [[ -n "$LANGUAGE" ]]; then
  SMOKE_ARGS+=(--language "$LANGUAGE")
fi
if [[ "$NO_RESUME" != "1" ]]; then
  SMOKE_ARGS+=(--resume)
fi
"$PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" "${SMOKE_ARGS[@]}"

# Validate smoke test results
SMOKE_PRED="$RUN_DIR/predictions/${SMOKE_TEST_DATASET}.txt"
SMOKE_OK=0
if [[ -f "$SMOKE_PRED" ]]; then
  SMOKE_LINES=$(wc -l < "$SMOKE_PRED" || echo 0)
  SMOKE_NONEMPTY=$(awk -F'\t' '$2!="" {print}' "$SMOKE_PRED" | wc -l || echo 0)
  if [[ "$SMOKE_NONEMPTY" -ge 1 ]]; then
    SMOKE_OK=1
  fi
fi

if [[ "$SMOKE_OK" != "1" ]]; then
  echo "ERROR: Smoke test failed."
  exit 1
fi

echo "Smoke test passed. Proceeding to full run..."

# [3/5] Generate predictions for all datasets
echo "[3/5] Generating predictions..."
for dataset in cs_dialogue kespeech voxpopuli aishell5 contextasr_en contextasr_zh; do
  echo "  -> Generating predictions for $dataset"
  GEN_ARGS=(
    --config "$RUN_DIR/custom_config.yaml"
    --model-dir "$MODEL_DIR"
    --dataset "$dataset"
    --run-dir "$RUN_DIR"
    --tool-name "$TOOL_NAME"
  )
  if [[ -n "$LANGUAGE" ]]; then
    GEN_ARGS+=(--language "$LANGUAGE")
  fi
  if [[ "$MAX_SAMPLES" != "0" ]]; then
    GEN_ARGS+=(--max-samples "$MAX_SAMPLES")
  fi
  if [[ "$NO_RESUME" != "1" ]]; then
    GEN_ARGS+=(--resume)
  fi
  "$PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" "${GEN_ARGS[@]}"
done

# Early exit if only preparing predictions
if [[ "$SKIP_VALIDATE_AND_EVAL" == "1" ]]; then
  echo "Skipping validation and evaluation"
  exit 0
fi

# [4/5] Validate predictions
echo "[4/5] Validating predictions..."
"$PYTHON_BIN" "$REPO_ROOT/scripts/validate_prediction_files.py" \
  --config "$RUN_DIR/custom_config.yaml" \
  --dataset cs_dialogue kespeech voxpopuli aishell5 contextasr_en contextasr_zh \
  --pred-dir "$RUN_DIR/predictions" \
  --require-nonempty \
  --output "$RUN_DIR/validation_payload.json"

# [5/5] Evaluate predictions
echo "[5/5] Evaluating predictions..."
"$PYTHON_BIN" "$REPO_ROOT/scripts/evaluate_predictions.py" \
  --config "$RUN_DIR/custom_config.yaml" \
  --dataset cs_dialogue kespeech voxpopuli aishell5 contextasr_en contextasr_zh \
  --pred-dir "$RUN_DIR/predictions" \
  --tool-name "$MODEL_NAME" \
  --results-dir "$RESULTS_DIR" \
  --protocol-id "$PROTOCOL_ID" \
  --model-dir "$MODEL_DIR" \
  --record \
  --output "$RUN_DIR/evaluation_payload.json"

echo "========================================"
echo "Run completed: $RUN_ID"
echo "Results: $RUN_DIR/evaluation_payload.json"
echo "========================================"
