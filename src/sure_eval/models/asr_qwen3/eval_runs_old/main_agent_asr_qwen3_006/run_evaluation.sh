#!/usr/bin/env bash
set -euo pipefail

# SURE-EVAL Main Flow Shell Entrypoint
# Materialized for run: main_agent_asr_qwen3_006
# Model: asr_qwen3
# Datasets: aishell1 librispeech_clean

# Friendly error reporter on uncaught failure
trap 'echo ""; echo "ERROR at line $LINENO. Run directory: ${RUN_DIR:-unknown}"; echo "Check logs and artifacts before retrying."' ERR

REPO_ROOT="/mnt/cloudstorfs/sjtu_home/junhao.du/sure-eval-sandbox"

# Runtime guard: verify REPO_ROOT points to a real SURE-EVAL repo
if [[ ! -d "$REPO_ROOT/scripts" ]]; then
  echo "ERROR: REPO_ROOT does not look like a SURE-EVAL repository root: $REPO_ROOT"
  exit 1
fi

MODEL_NAME="asr_qwen3"
MODEL_DIR="$REPO_ROOT/src/sure_eval/models/$MODEL_NAME"
RUN_ID="main_agent_asr_qwen3_006"
RUN_DIR="$MODEL_DIR/eval_runs/$RUN_ID"
TOOL_NAME="asr_transcribe"
PYTHON_BIN="/mnt/cloudstorfs/sjtu_home/junhao.du/sure-eval-sandbox/.venv/bin/python"
MAX_SAMPLES="0"
SKIP_VALIDATE_AND_EVAL="0"
NO_RESUME="0"
LANGUAGE=""
export HF_HUB_CACHE="$MODEL_DIR/.runtime/hf_cache"
export MODELSCOPE_CACHE="/hpc_stor03/sjtu_home/yixuan.wang/workspace/sure-eval-sandbox/src/sure_eval/models/asr_qwen3/.runtime/modelscope_cache"

# ---------------------------------------------------------------------------
# Device resolution
# ---------------------------------------------------------------------------
DEVICE="cuda:1"

# ---------------------------------------------------------------------------
# Dataset handling
# ---------------------------------------------------------------------------
DATASETS="aishell1 librispeech_clean"
read -ra DATASET_ARRAY <<< "$DATASETS"

# Guard against empty dataset list
if [[ ${#DATASET_ARRAY[@]} -eq 0 ]]; then
  echo "ERROR: No datasets specified. Set DATASETS (space-separated) or DATASET."
  exit 1
fi

mkdir -p "$RUN_DIR/predictions/logs"

echo "========================================"
echo "SURE-EVAL Run: $RUN_ID"
echo "Model: $MODEL_NAME"
echo "Datasets: ${DATASET_ARRAY[*]}"
echo "MAX_SAMPLES: $MAX_SAMPLES"
echo "SKIP_VALIDATE_AND_EVAL: $SKIP_VALIDATE_AND_EVAL"
echo "========================================"

# ---------------------------------------------------------------------------
# [1/5] Prepare datasets
# ---------------------------------------------------------------------------
echo "[1/5] Preparing datasets..."
"$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_sure_dataset.py" \
  --dataset "${DATASET_ARRAY[@]}" \
  --output "$RUN_DIR/prepare_summary.json"

# ---------------------------------------------------------------------------
# [2/5] Materialize prediction templates
# ---------------------------------------------------------------------------
echo "[2/5] Materializing prediction templates..."
"$PYTHON_BIN" "$REPO_ROOT/scripts/materialize_predictions_template.py" \
  --dataset "${DATASET_ARRAY[@]}" \
  --output-dir "$RUN_DIR/predictions" \
  --manifest-name manifest.json \
  --overwrite

# ---------------------------------------------------------------------------
# [2.5/5] Smoke test gate (first dataset only)
# ---------------------------------------------------------------------------
SMOKE_TEST_DATASET="${DATASET_ARRAY[0]}"
SMOKE_TEST_SAMPLES="10"
echo "[2.5/5] Smoke test on $SMOKE_TEST_DATASET (${SMOKE_TEST_SAMPLES} samples)..."

SMOKE_ARGS=(
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
if [[ -n "$DEVICE" ]]; then
  SMOKE_ARGS+=(--device "$DEVICE")
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
  echo "ERROR: Smoke test failed. No valid predictions in first ${SMOKE_TEST_SAMPLES} samples."
  echo "Check: $RUN_DIR/predictions/logs/${SMOKE_TEST_DATASET}_results.log"
  exit 1
fi

echo "Smoke test passed (${SMOKE_NONEMPTY}/${SMOKE_LINES} valid). Proceeding to full run..."

# ---------------------------------------------------------------------------
# [3/5] Generate predictions for all datasets
# ---------------------------------------------------------------------------
echo "[3/5] Generating predictions..."
for dataset in "${DATASET_ARRAY[@]}"; do
  echo "  -> Generating predictions for $dataset"
  GEN_ARGS=(
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
  if [[ -n "$DEVICE" ]]; then
    GEN_ARGS+=(--device "$DEVICE")
  fi
  "$PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" "${GEN_ARGS[@]}"
done

# ---------------------------------------------------------------------------
# [3.5/5] Write prediction generation status summary
# ---------------------------------------------------------------------------
echo "Writing prediction generation status..."
PRED_STATUS_FILE="$RUN_DIR/prediction_generation_status.json"
cat > "$PRED_STATUS_FILE" <<EOF
{
  "run_id": "$RUN_ID",
  "model_name": "$MODEL_NAME",
  "execution_path": "direct_server_use",
  "tool_name": "$TOOL_NAME",
  "datasets": [
EOF

FIRST=1
for dataset in "${DATASET_ARRAY[@]}"; do
  PRED_FILE="$RUN_DIR/predictions/${dataset}.txt"
  NUM_LINES=0
  if [[ -f "$PRED_FILE" ]]; then
    NUM_LINES=$(wc -l < "$PRED_FILE" || echo 0)
  fi
  if [[ "$FIRST" == "1" ]]; then
    FIRST=0
  else
    echo "," >> "$PRED_STATUS_FILE"
  fi
  cat >> "$PRED_STATUS_FILE" <<EOF
    {
      "dataset": "$dataset",
      "prediction_file": "$PRED_FILE",
      "status": "completed",
      "num_expected_samples": "see_manifest",
      "num_generated_samples": $NUM_LINES,
      "log_path": "$RUN_DIR/predictions/logs/${dataset}.log"
    }
EOF
done

cat >> "$PRED_STATUS_FILE" <<EOF

  ]
}
EOF

# ---------------------------------------------------------------------------
# Early exit if only preparing predictions
# ---------------------------------------------------------------------------
if [[ "$SKIP_VALIDATE_AND_EVAL" == "1" ]]; then
  echo "Skipping validation and evaluation by request (SKIP_VALIDATE_AND_EVAL=1)"
  echo "Run prepared through prediction generation: $RUN_DIR"
  exit 0
fi

# ---------------------------------------------------------------------------
# [4/5] Validate predictions
# ---------------------------------------------------------------------------
echo "[4/5] Validating predictions..."
"$PYTHON_BIN" "$REPO_ROOT/scripts/validate_prediction_files.py" \
  --dataset "${DATASET_ARRAY[@]}" \
  --pred-dir "$RUN_DIR/predictions" \
  --require-nonempty \
  --output "$RUN_DIR/validation_payload.json"

# ---------------------------------------------------------------------------
# [5/5] Evaluate predictions
# ---------------------------------------------------------------------------
EVAL_EXIT=0
echo "[5/5] Evaluating predictions..."
"$PYTHON_BIN" "$REPO_ROOT/scripts/evaluate_predictions.py" \
  --dataset "${DATASET_ARRAY[@]}" \
  --pred-dir "$RUN_DIR/predictions" \
  --tool-name "$MODEL_NAME" \
  --record \
  --output "$RUN_DIR/evaluation_payload.json" || EVAL_EXIT=$?

if [[ "$EVAL_EXIT" != "0" ]]; then
  echo "WARNING: Evaluation exited with code $EVAL_EXIT"
  echo "Run directory: $RUN_DIR"
  echo "Check predictions and logs before deciding next step."
fi

echo "========================================"
echo "Run completed: $RUN_ID"
echo "Results: $RUN_DIR/evaluation_payload.json"
echo "========================================"
