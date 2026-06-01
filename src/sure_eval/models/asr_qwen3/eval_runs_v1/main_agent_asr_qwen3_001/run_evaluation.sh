#!/usr/bin/env bash
set -uo pipefail

# SURE-EVAL Execution Surface for asr_qwen3
# Derived from templates/run_single_model_single_dataset.sh
# Wraps the single-dataset template for each selected dataset.

REPO_ROOT="/mnt/cloudstorfs/sjtu_home/junhao.du/sure-eval-sandbox"
MODEL_NAME="asr_qwen3"
MODEL_DIR="$REPO_ROOT/src/sure_eval/models/$MODEL_NAME"
RUN_ID="main_agent_asr_qwen3_001"
TOOL_NAME="asr_transcribe"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
RESULTS_DIR="$REPO_ROOT/results/$MODEL_NAME/strict_core"
PROTOCOL_ID="strict_core"

# Per-dataset sub-runs to avoid summary file collisions
DATASETS="aishell1 librispeech_clean"
read -ra DATASET_ARRAY <<< "$DATASETS"

OVERALL_EXIT=0

for DATASET in "${DATASET_ARRAY[@]}"; do
  echo "========================================"
  echo "Starting sub-run for dataset: $DATASET"
  echo "========================================"

  export DATASET
  export REPO_ROOT
  export MODEL_NAME
  export MODEL_DIR
  export RUN_ID
  export TOOL_NAME
  export PYTHON_BIN
  export RESULTS_DIR
  export PROTOCOL_ID
  # Override RUN_DIR so each dataset gets its own subdirectory
  export RUN_DIR="$MODEL_DIR/eval_runs/$RUN_ID/$DATASET"

  mkdir -p "$RUN_DIR/predictions/logs"

  bash "$REPO_ROOT/templates/run_single_model_single_dataset.sh"
  SUB_EXIT=$?

  if [[ "$SUB_EXIT" != "0" ]]; then
    echo "ERROR: Sub-run for $DATASET exited with code $SUB_EXIT"
    OVERALL_EXIT=$SUB_EXIT
  else
    echo "Sub-run for $DATASET completed successfully"
  fi

done

# ---------------------------------------------------------------------------
# Aggregate per-dataset status into top-level prediction_generation_status.json
# ---------------------------------------------------------------------------
echo "Aggregating run status..."
PRED_STATUS_FILE="$MODEL_DIR/eval_runs/$RUN_ID/prediction_generation_status.json"
mkdir -p "$(dirname "$PRED_STATUS_FILE")"

cat > "$PRED_STATUS_FILE" <<EOF
{
  "run_id": "$RUN_ID",
  "model_name": "$MODEL_NAME",
  "execution_path": "direct_server_use",
  "protocol_id": "$PROTOCOL_ID",
  "tool_name": "$TOOL_NAME",
  "datasets": [
EOF

FIRST=1
for DATASET in "${DATASET_ARRAY[@]}"; do
  SUB_RUN_DIR="$MODEL_DIR/eval_runs/$RUN_ID/$DATASET"
  PRED_FILE="$SUB_RUN_DIR/predictions/${DATASET}.txt"
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
      "dataset": "$DATASET",
      "prediction_file": "$PRED_FILE",
      "status": "completed",
      "num_generated_samples": $NUM_LINES,
      "log_path": "$SUB_RUN_DIR/predictions/logs/${DATASET}.log"
    }
EOF
done

cat >> "$PRED_STATUS_FILE" <<EOF

  ]
}
EOF

echo "========================================"
echo "All sub-runs finished. Overall exit: $OVERALL_EXIT"
echo "Run directory: $MODEL_DIR/eval_runs/$RUN_ID"
echo "========================================"

exit $OVERALL_EXIT
