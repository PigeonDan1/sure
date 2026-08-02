#!/usr/bin/env bash
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"
MODEL_NAME="${MODEL_NAME:-my_model}"
SHARED_MODEL_ROOT="${SHARED_MODEL_ROOT:-<shared-model-root>}"
REPO_MODEL_ROOT="${REPO_MODEL_ROOT:-$REPO_ROOT/src/sure_eval/models}"
if [[ -z "${MODEL_DIR:-}" ]]; then
  if [[ -d "$SHARED_MODEL_ROOT/$MODEL_NAME" ]]; then
    MODEL_DIR="$SHARED_MODEL_ROOT/$MODEL_NAME"
    MODEL_DIR_SOURCE="shared_model_root"
  else
    MODEL_DIR="$REPO_MODEL_ROOT/$MODEL_NAME"
    MODEL_DIR_SOURCE="repo_model_root"
  fi
else
  MODEL_DIR_SOURCE="explicit_model_dir"
fi
if [[ ! -d "$MODEL_DIR" ]]; then
  echo "ERROR: Model directory not found: $MODEL_DIR"
  echo "Checked shared root: $SHARED_MODEL_ROOT/$MODEL_NAME"
  echo "Checked repo root: $REPO_MODEL_ROOT/$MODEL_NAME"
  exit 1
fi
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
METRICS="${METRICS:-}"
AUDIO_EVAL_TASKS="${AUDIO_EVAL_TASKS:-TTS VC}"

mkdir -p "$RUN_DIR/predictions/logs"

echo "[1/5] prepare dataset"
"$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_sure_dataset.py" \
  --dataset "$DATASET" \
  --output "$RUN_DIR/prepare_summary.json"

mapfile -t EXPANDED_DATASETS < <("$PYTHON_BIN" - "$RUN_DIR/prepare_summary.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
for item in payload.get("prepared", []):
    dataset = item.get("dataset")
    if dataset:
        print(dataset)
PY
)
if [[ ${#EXPANDED_DATASETS[@]} -ne 1 ]]; then
  echo "ERROR: single-dataset template requires one concrete dataset split; '$DATASET' expanded to: ${EXPANDED_DATASETS[*]}"
  exit 1
fi
DATASET="${EXPANDED_DATASETS[0]}"
echo "Concrete dataset: $DATASET"

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

AUDIO_EVAL_REQUIRED=$("$PYTHON_BIN" - "$RUN_DIR/validation_payload.json" "$AUDIO_EVAL_TASKS" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
audio_tasks = {item.upper() for item in sys.argv[2].split()}
datasets = [item.get("dataset") for item in payload.get("results") or [] if item.get("dataset")]
required = False
for dataset in datasets:
    path = Path("data/datasets/sure_benchmark/jsonl") / f"{dataset}.jsonl"
    if not path.exists():
        continue
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        if str(sample.get("task", "")).upper() in audio_tasks:
            required = True
        break
print("1" if required else "0")
PY
)

if [[ "$AUDIO_EVAL_REQUIRED" == "1" ]]; then
  AUDIO_EVAL_METRICS="$METRICS"
  if [[ -z "$AUDIO_EVAL_METRICS" ]]; then
    AUDIO_EVAL_METRICS=$("$PYTHON_BIN" - "$DATASET" <<'PY'
import json
import sys
from pathlib import Path

dataset = sys.argv[1]
path = Path("data/datasets/sure_benchmark/jsonl") / f"{dataset}.jsonl"
metrics = []
if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        task = str(sample.get("task", "")).upper()
        language = str(sample.get("language", "")).lower()
        if task == "TTS":
            metrics.append("tts_cer" if language.startswith(("zh", "cmn", "yue")) else "tts_wer")
            metrics.extend(["sim/wavlm-large", "sim/ecapa-tdnn", "sim/eres2net", "dnsmos", "wv-mos", "utmos"])
        elif task == "VC":
            metrics.append("vc_cer" if language.startswith(("zh", "cmn", "yue")) else "vc_wer")
            metrics.extend(["sim/wavlm-large", "sim/ecapa-tdnn", "sim/eres2net", "dnsmos", "wv-mos", "utmos"])
        break
deduped = []
for metric in metrics:
    if metric and metric not in deduped:
        deduped.append(metric)
print(" ".join(deduped))
PY
)
  fi
  cat > "$RUN_DIR/evaluation_handoff.json" <<EOF
{
  "run_id": "$RUN_ID",
  "status": "prediction_complete_evaluation_pending",
  "reason": "TTS/VC audio metrics must run through src/sure_eval/evaluation using node-local uv environments and checkpoints; the inference image is not the metric dependency surface.",
  "contract": "docs/agents/main_flow_agent/contracts/tts_vc_audio_evaluation_surface.md",
  "template": "docs/agents/main_flow_agent/templates/run_audio_evaluation_only.sh",
  "run_dir": "$RUN_DIR",
  "model_name": "$MODEL_NAME",
  "model_dir": "$MODEL_DIR",
  "datasets": "$DATASET",
  "metrics": "$AUDIO_EVAL_METRICS",
  "results_dir": "$RESULTS_DIR",
  "protocol_id": "$PROTOCOL_ID",
  "evaluation_runtime": "src/sure_eval/evaluation node-local uv environments; vc image, if used, is only the base runtime/interpreter shell",
  "next_action": "Materialize run_audio_evaluation_only.sh and run evaluation through src/sure_eval/evaluation. If cluster GPU execution is required, submit vc jobs that call the node-local providers, then merge the segment payloads.",
  "metric_execution_plan": {
    "segmentation_required_for_full_suite": true,
    "segments": [
      "segment_tts_semantic",
      "segment_tts_speaker_wavlm_ecapa",
      "segment_tts_speaker_eres2net",
      "segment_tts_mos_dnsmos",
      "segment_tts_mos_wvmos",
      "segment_tts_mos_utmos"
    ]
  }
}
EOF
  echo "Audio evaluation handoff written: $RUN_DIR/evaluation_handoff.json"
  echo "TTS/VC evaluation is intentionally not run in the model inference surface."
  echo "Next template: docs/agents/main_flow_agent/templates/run_audio_evaluation_only.sh"
  exit 0
fi

# Non-audio tasks such as ASR/S2TT/SER/GR/SLU/SD/SA-ASR continue through the
# standard deterministic evaluator below.

echo "[5/5] evaluate predictions"
EVAL_EXIT=0
EVAL_ARGS=(
  --dataset "$DATASET"
  --pred-dir "$RUN_DIR/predictions"
  --tool-name "$MODEL_NAME"
  --results-dir "$RESULTS_DIR"
  --protocol-id "$PROTOCOL_ID"
  --model-dir "$MODEL_DIR"
  --run-dir "$RUN_DIR"
  --validation-payload "$RUN_DIR/validation_payload.json"
  --output "$RUN_DIR/evaluation_payload.json"
)
if [[ -n "$METRICS" ]]; then
  read -r -a METRIC_ARRAY <<< "$METRICS"
  for metric in "${METRIC_ARRAY[@]}"; do
    EVAL_ARGS+=(--metric "$metric")
  done
fi
"$PYTHON_BIN" "$REPO_ROOT/scripts/evaluate_predictions.py" \
  "${EVAL_ARGS[@]}" || EVAL_EXIT=$?

if [[ "$EVAL_EXIT" != "0" ]]; then
  echo "ERROR: Evaluation exited with code $EVAL_EXIT" >&2
  echo "Run directory: $RUN_DIR" >&2
  echo "Check predictions and logs before deciding next step." >&2
  exit "$EVAL_EXIT"
fi

"$PYTHON_BIN" "$REPO_ROOT/scripts/generate_report_snapshot.py" \
  --run-dir "$RUN_DIR" \
  --output "$RUN_DIR/report_snapshot.md" || echo "WARNING: Report snapshot generation failed (non-fatal)"

if [[ -f "$RUN_DIR/report_snapshot.md" ]]; then
  mkdir -p "$RESULTS_DIR"
  cp "$RUN_DIR/report_snapshot.md" "$RESULTS_DIR/report_snapshot.md"
fi

echo "Run completed (with possible warnings): $RUN_DIR"
