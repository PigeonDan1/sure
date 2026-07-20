#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${REPO_ROOT:-}" ]]; then
  for candidate in \
    "$SCRIPT_DIR/.." \
    "$SCRIPT_DIR/../.." \
    "$SCRIPT_DIR/../../.." \
    "$SCRIPT_DIR/../../../.." \
    "$SCRIPT_DIR/../../../../sure/skills/sure_eval" \
    "$PWD" \
    "$PWD/sure/skills/sure_eval"
  do
    if [[ -f "$candidate/scripts/prepare_sure_dataset.py" ]]; then
      REPO_ROOT="$(cd "$candidate" && pwd)"
      break
    fi
  done
fi
if [[ -z "${REPO_ROOT:-}" ]]; then
  echo "ERROR: Could not locate the sure_eval skill script root. Set REPO_ROOT explicitly."
  exit 1
fi
MODEL_NAME="${MODEL_NAME:-my_model}"
PYTHON_BIN="${PYTHON_BIN:-python}"
HARNESS_PYTHON_BIN="${HARNESS_PYTHON_BIN:-$PYTHON_BIN}"
MODEL_RESOLUTION_JSON="${MODEL_RESOLUTION_JSON:-}"
if [[ -z "${MODEL_DIR:-}" ]]; then
  MODEL_RESOLVE_ARGS=(--model "$MODEL_NAME" --require-verdict --require-runtime-files)
  if [[ -n "$MODEL_RESOLUTION_JSON" ]]; then
    MODEL_RESOLVE_ARGS+=(--output "$MODEL_RESOLUTION_JSON")
  fi
  MODEL_DIR="$("$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/resolve_model_dir.py" "${MODEL_RESOLVE_ARGS[@]}" | "$HARNESS_PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["model_dir"] or "")')"
fi
if [[ -z "$MODEL_DIR" || ! -d "$MODEL_DIR" ]]; then
  echo "ERROR: Model directory not found or not runtime-ready for MODEL_NAME=$MODEL_NAME"
  echo "Set MODEL_DIR directly, or set SURE_MODELS_DIR/SURE_MODEL_ROOT/LEGACY_SURE_MODELS_DIR/LEGACY_SURE_EVAL_ROOT."
  exit 1
fi
DATASET="${DATASET:-aishell1}"
RUN_ID="${RUN_ID:-main_agent_${MODEL_NAME}_$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="${RUN_DIR:-$MODEL_DIR/eval_runs/$RUN_ID}"
TOOL_NAME="${TOOL_NAME:-}"
if [[ -z "$TOOL_NAME" && -f "$MODEL_DIR/config.yaml" ]]; then
  TOOL_NAME="$("$HARNESS_PYTHON_BIN" - "$MODEL_DIR/config.yaml" <<'PY'
from pathlib import Path
import sys
import yaml

config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
tools = config.get("tools") or []
for tool in tools:
    if isinstance(tool, dict) and tool.get("name"):
        print(str(tool["name"]))
        break
else:
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    task = str(model.get("task") or config.get("task") or config.get("task_type") or "").strip().upper()
    defaults = {
        "ASR": "transcribe_audio",
        "S2TT": "translate_audio",
        "TTS": "synthesize_speech",
        "VC": "convert_voice",
    }
    if task in defaults:
        print(defaults[task])
PY
)"
fi
TOOL_NAME="${TOOL_NAME:-transcribe_audio}"
LANGUAGE="${LANGUAGE:-}"
DEVICE="${DEVICE:-${DEVICE_RESOLVED:-${SURE_EVAL_DEVICE:-}}}"
MAX_SAMPLES="${MAX_SAMPLES:-0}"
SKIP_VALIDATE_AND_EVAL="${SKIP_VALIDATE_AND_EVAL:-0}"
RESULTS_DIR="${RESULTS_DIR:-$RUN_DIR/results}"
PROTOCOL_ID="${PROTOCOL_ID:-strict_core}"
METRICS="${METRICS:-}"
EVALUATION_BACKEND="${EVALUATION_BACKEND:-external}"
STRICT_MAIN_FLOW="${STRICT_MAIN_FLOW:-1}"
AUDIO_EVAL_TASKS="${AUDIO_EVAL_TASKS:-TTS VC}"
EXECUTION_PATH="${SURE_EVAL_EXECUTION_PATH:-${EXECUTION_PATH:-unknown}}"

DEVICE_REQUEST="$DEVICE"
DEVICE_ACTUAL="$DEVICE"
if [[ "$DEVICE_REQUEST" =~ ^cuda:([0-9]+)$ ]]; then
  export CUDA_VISIBLE_DEVICES="${BASH_REMATCH[1]}"
  DEVICE_ACTUAL="cuda:0"
  DEVICE="$DEVICE_ACTUAL"
elif [[ "${DEVICE_REQUEST,,}" == "cpu" ]]; then
  export CUDA_VISIBLE_DEVICES=""
  DEVICE_ACTUAL="cpu"
  DEVICE="cpu"
fi
export DEVICE
export HARNESS_PYTHON_BIN
export SURE_EVAL_METRICS="$METRICS"
export SURE_EVAL_DEVICE_REQUEST="$DEVICE_REQUEST"
export SURE_EVAL_DEVICE_ACTUAL="$DEVICE_ACTUAL"
export SURE_EVAL_EXECUTION_PATH="$EXECUTION_PATH"

if [[ -z "${SURE_EVAL_CONFIG:-}" ]]; then
  HARNESS_REPO_ROOT="$(cd "$REPO_ROOT/../../.." && pwd)"
  BASE_SURE_EVAL_CONFIG="$HARNESS_REPO_ROOT/sure/external/sure-evaluation/config/default.yaml"
  SURE_EVAL_DATASETS_ROOT="${SURE_EVAL_DATASETS_ROOT:-$HARNESS_REPO_ROOT/data/datasets}"
  if [[ ! -f "$BASE_SURE_EVAL_CONFIG" ]]; then
    echo "ERROR: SURE_EVAL_CONFIG is unset and submodule config is missing: $BASE_SURE_EVAL_CONFIG"
    exit 2
  fi
  if [[ ! -d "$SURE_EVAL_DATASETS_ROOT/sure_benchmark/jsonl" ]]; then
    echo "ERROR: SURE_EVAL_DATASETS_ROOT must contain sure_benchmark/jsonl: $SURE_EVAL_DATASETS_ROOT"
    exit 2
  fi
  SURE_EVAL_CONFIG="$RUN_DIR/_harness_config.yaml"
  mkdir -p "$RUN_DIR"
  "$HARNESS_PYTHON_BIN" - "$BASE_SURE_EVAL_CONFIG" "$SURE_EVAL_CONFIG" "$HARNESS_REPO_ROOT" "$RUN_DIR" "$SURE_EVAL_DATASETS_ROOT" <<'PY'
from pathlib import Path
import sys
import yaml

base_config = Path(sys.argv[1])
output_config = Path(sys.argv[2])
harness_repo = Path(sys.argv[3])
run_dir = Path(sys.argv[4])
datasets_root = Path(sys.argv[5])
config = yaml.safe_load(base_config.read_text(encoding="utf-8")) or {}
data = dict(config.get("data") or {})
data.update(
    {
        "root": str(harness_repo / "data"),
        "cache": str(harness_repo / "data" / "cache"),
        "models": str(harness_repo / "data" / "models"),
        "datasets": str(datasets_root),
        "results": str(run_dir / "results"),
    }
)
config["data"] = data
output_config.parent.mkdir(parents=True, exist_ok=True)
output_config.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
PY
fi
export SURE_EVAL_CONFIG

mkdir -p "$RUN_DIR/predictions/logs"

echo "========================================"
echo "SURE-EVAL Run: $RUN_ID"
echo "Model: $MODEL_NAME"
echo "Model dir: $MODEL_DIR"
echo "Dataset: $DATASET"
echo "Harness Python: $HARNESS_PYTHON_BIN"
echo "Execution path: $EXECUTION_PATH"
echo "DEVICE_REQUEST: ${DEVICE_REQUEST:-auto/from-runtime}"
echo "DEVICE_ACTUAL: ${DEVICE_ACTUAL:-auto/from-runtime}"
echo "CUDA_VISIBLE_DEVICES: ${CUDA_VISIBLE_DEVICES-<unset>}"
echo "MAX_SAMPLES: $MAX_SAMPLES"
echo "========================================"

echo "[1/5] prepare dataset"
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/prepare_sure_dataset.py" \
  --dataset "$DATASET" \
  --output "$RUN_DIR/prepare_summary.json"

mapfile -t EXPANDED_DATASETS < <("$HARNESS_PYTHON_BIN" - "$RUN_DIR/prepare_summary.json" <<'PY'
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
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/materialize_predictions_template.py" \
  --dataset "$DATASET" \
  --output-dir "$RUN_DIR/predictions" \
  --manifest-name manifest.json \
  --overwrite

# [2.5/5] Smoke test gate
if [[ -z "${SMOKE_TEST_SAMPLES:-}" ]]; then
  if [[ "$MAX_SAMPLES" =~ ^[0-9]+$ && "$MAX_SAMPLES" != "0" && "$MAX_SAMPLES" -lt 10 ]]; then
    SMOKE_TEST_SAMPLES="$MAX_SAMPLES"
  else
    SMOKE_TEST_SAMPLES="10"
  fi
fi
echo "[2.5/5] smoke test (${SMOKE_TEST_SAMPLES} samples)..."
SMOKE_ARGS=(
  --model-dir "$MODEL_DIR"
  --dataset "$DATASET"
  --run-dir "$RUN_DIR"
  --tool-name "$TOOL_NAME"
  --max-samples "$SMOKE_TEST_SAMPLES"
  --resume
)
if [[ -n "$LANGUAGE" ]]; then
  SMOKE_ARGS+=(--language "$LANGUAGE")
fi
if [[ -n "$DEVICE" ]]; then
  SMOKE_ARGS+=(--device "$DEVICE")
fi
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" "${SMOKE_ARGS[@]}"

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
if [[ "${SMOKE_ONLY:-0}" == "1" ]]; then
  echo "Smoke-only mode requested; stopping after smoke gate."
  exit 0
fi

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
if [[ -n "$DEVICE" ]]; then
  GEN_ARGS+=(--device "$DEVICE")
fi
if [[ "${NO_RESUME:-0}" != "1" ]]; then
  GEN_ARGS+=(--resume)
fi
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" "${GEN_ARGS[@]}"

if [[ "$SKIP_VALIDATE_AND_EVAL" == "1" ]]; then
  echo "Skipping validation and evaluation by request"
  echo "Run prepared through prediction generation: $RUN_DIR"
  exit 0
fi

echo "[4/5] validate predictions"
VALIDATE_ARGS=(
  --dataset "$DATASET"
  --pred-dir "$RUN_DIR/predictions"
  --require-nonempty
  --output "$RUN_DIR/validation_payload.json"
)
if [[ "$MAX_SAMPLES" != "0" ]]; then
  VALIDATE_ARGS+=(--max-samples "$MAX_SAMPLES")
fi
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/validate_prediction_files.py" "${VALIDATE_ARGS[@]}"

AUDIO_EVAL_REQUIRED=$("$HARNESS_PYTHON_BIN" - "$RUN_DIR/prepare_summary.json" "$AUDIO_EVAL_TASKS" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
audio_tasks = {item.upper() for item in sys.argv[2].split()}
required = False
for item in summary.get("prepared") or []:
    jsonl_path = Path(str(item.get("jsonl_path") or ""))
    if not jsonl_path.is_absolute():
        jsonl_path = Path.cwd() / jsonl_path
    if not jsonl_path.exists():
        continue
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
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
    AUDIO_EVAL_METRICS=$("$HARNESS_PYTHON_BIN" - "$RUN_DIR/prepare_summary.json" "$REPO_ROOT" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
repo_root = Path(sys.argv[2])
sys.path.insert(0, str(repo_root / "scripts"))
from evaluation_capabilities import supported_metrics_for_task_language
from resolve_evaluation_engine import resolve_engine_root

engine = resolve_engine_root(None)
metrics = []
for item in summary.get("prepared") or []:
    jsonl_path = Path(str(item.get("jsonl_path") or ""))
    if not jsonl_path.is_absolute():
        jsonl_path = Path.cwd() / jsonl_path
    if not jsonl_path.exists():
        continue
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        task = str(sample.get("task", "")).upper()
        language = str(sample.get("language", "")).lower()
        if engine is not None:
            metrics.extend(
                metric
                for metric in supported_metrics_for_task_language(engine[1], task, language)
                if metric != "multi"
            )
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
  "template": "scripts/templates/run_audio_evaluation_only.sh",
  "run_dir": "$RUN_DIR",
  "model_name": "$MODEL_NAME",
  "model_dir": "$MODEL_DIR",
  "datasets": "$DATASET",
  "metrics": "$AUDIO_EVAL_METRICS",
  "device": "$DEVICE",
  "results_dir": "$RESULTS_DIR",
  "protocol_id": "$PROTOCOL_ID",
  "evaluation_runtime": "src/sure_eval/evaluation node-local uv environments; vc image, if used, is only the base runtime/interpreter shell",
  "next_action": "Run scripts/templates/run_audio_evaluation_only.sh through the harness evaluation surface. If cluster GPU execution is required, submit vc jobs that call the node-local providers, then merge the segment payloads.",
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
  echo "Next template: scripts/templates/run_audio_evaluation_only.sh"
  exit 0
fi

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
  --evaluation-backend "$EVALUATION_BACKEND"
  --output "$RUN_DIR/evaluation_payload.json"
)
if [[ "$STRICT_MAIN_FLOW" == "1" ]]; then
  EVAL_ARGS+=(--strict-main-flow)
fi
if [[ -n "$METRICS" ]]; then
  read -r -a METRIC_ARRAY <<< "$METRICS"
  for metric in "${METRIC_ARRAY[@]}"; do
    EVAL_ARGS+=(--metric "$metric")
  done
fi
if [[ -n "$DEVICE" ]]; then
  EVAL_ARGS+=(--device "$DEVICE")
fi
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/evaluate_predictions.py" "${EVAL_ARGS[@]}" || EVAL_EXIT=$?

if [[ "$EVAL_EXIT" != "0" ]]; then
  echo "WARNING: Evaluation exited with code $EVAL_EXIT"
  echo "Run directory: $RUN_DIR"
  echo "Check predictions and logs before deciding next step."
fi

"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/generate_report_snapshot.py" \
  --run-dir "$RUN_DIR" \
  --output "$RUN_DIR/report_snapshot.md" || echo "WARNING: Report snapshot generation failed (non-fatal)"

if [[ -f "$RUN_DIR/report_snapshot.md" ]]; then
  mkdir -p "$RESULTS_DIR"
  cp "$RUN_DIR/report_snapshot.md" "$RESULTS_DIR/report_snapshot.md"
fi

echo "Run completed (with possible warnings): $RUN_DIR"
