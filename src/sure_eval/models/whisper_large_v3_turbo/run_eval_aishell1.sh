#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$MODEL_DIR/../../../../" && pwd)"
RUN_ID="${RUN_ID:-main_agent_whisper_large_v3_turbo_aishell1_$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="$MODEL_DIR/eval_runs/$RUN_ID"
PYTHON_BIN="${PYTHON_BIN:-python}"
MAX_SAMPLES="${MAX_SAMPLES:-0}"
SKIP_VALIDATE_AND_EVAL="${SKIP_VALIDATE_AND_EVAL:-0}"
MODEL_PYTHON="${MODEL_PYTHON:-/root/.cache/sure_eval_whisper_large_v3_turbo/.venv/bin/python}"
HF_HOME="${HF_HOME:-/root/.cache/sure_eval_whisper_large_v3_turbo/hf-home}"

mkdir -p "$RUN_DIR/predictions/logs"
export MODEL_PYTHON
export HF_HOME

echo "[1/5] prepare dataset"
"$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_sure_dataset.py" \
  --dataset aishell1 \
  --output "$RUN_DIR/prepare_summary.json"

echo "[2/5] materialize prediction template"
"$PYTHON_BIN" "$REPO_ROOT/scripts/materialize_predictions_template.py" \
  --dataset aishell1 \
  --output-dir "$RUN_DIR/predictions" \
  --manifest-name manifest.json

echo "[3/5] generate predictions via direct server use"
GEN_ARGS=(
  --model-dir "$MODEL_DIR"
  --dataset aishell1
  --run-dir "$RUN_DIR"
  --tool-name transcribe_audio
)
if [[ "$MAX_SAMPLES" != "0" ]]; then
  GEN_ARGS+=(--max-samples "$MAX_SAMPLES")
fi
"$PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" \
  "${GEN_ARGS[@]}"

if [[ "$SKIP_VALIDATE_AND_EVAL" == "1" ]]; then
  echo "Skipping validation and evaluation by request"
  echo "Run prepared through prediction generation: $RUN_DIR"
  exit 0
fi

echo "[4/5] validate predictions"
"$PYTHON_BIN" "$REPO_ROOT/scripts/validate_prediction_files.py" \
  --dataset aishell1 \
  --pred-dir "$RUN_DIR/predictions" \
  --require-nonempty \
  --output "$RUN_DIR/validation_payload.json"

echo "[5/5] evaluate predictions"
"$PYTHON_BIN" "$REPO_ROOT/scripts/evaluate_predictions.py" \
  --dataset aishell1 \
  --pred-dir "$RUN_DIR/predictions" \
  --tool-name whisper_large_v3_turbo \
  --output "$RUN_DIR/evaluation_payload.json"

echo "Run completed: $RUN_DIR"
