#!/usr/bin/env bash
set -euo pipefail

# SURE-EVAL Main Flow Shell Entrypoint
# Supports single or multiple datasets via the DATASETS environment variable.
#
# Dataset paths accept an optional @<version_id> suffix.
# The suffix is REQUIRED when the dataset has multiple versions under sample_files/ —
# the strict resolver refuses ambiguous versions. Always carry the user's @<version_id>
# through unchanged; dropping it fails the smoke gate.
#
# Usage (single dataset):
#   DATASETS="${SURE_DATASET_SOURCE_ROOT}/group/store/ds_pool/example@v1.0.2" ./run_single_model.sh
#
# Usage (multiple datasets):
#   DATASETS="${SURE_DATASET_SOURCE_ROOT}/group/store/ds_pool/example-a ${SURE_DATASET_SOURCE_ROOT}/group/store/ds_pool/example-b" ./run_single_model.sh
#
# Usage (explicit metrics):
#   DATASETS="${SURE_DATASET_SOURCE_ROOT}/group/store/ds_pool/example" METRICS="cer wer" ./run_single_model.sh
#
# Usage (bounded smoke test only):
#   DATASETS="${SURE_DATASET_SOURCE_ROOT}/group/store/ds_pool/example" MAX_SAMPLES=10 ./run_single_model.sh
#
# Usage (skip validation and evaluation):
#   DATASETS="${SURE_DATASET_SOURCE_ROOT}/group/store/ds_pool/example" SKIP_VALIDATE_AND_EVAL=1 ./run_single_model.sh
#
# NOTE: The following variables SHOULD be explicitly set at materialization time
# by the main-flow agent. Defaults are provided for local testing only.
#   REPO_ROOT  - absolute path to the SURE-EVAL repository root
#   MODEL_NAME - model directory name under shared model root or src/sure_eval/models/
#   MODEL_DIR  - optional absolute model directory override
#   RUN_ID     - unique run identifier (e.g., main_agent_asr_qwen3_001)
#   TOOL_NAME  - MCP tool name declared in the model's config.yaml

# Friendly error reporter on uncaught failure
trap 'echo ""; echo "ERROR at line $LINENO. Run directory: ${RUN_DIR:-unknown}"; echo "Check logs and artifacts before retrying."' ERR

# REPO_ROOT should be overridden at materialization time.
# Default assumes this script lives under the harness sure_eval skill templates.
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"

# Runtime guard: verify REPO_ROOT points to a real SURE-EVAL repo
if [[ ! -d "$REPO_ROOT/scripts" ]]; then
  echo "ERROR: REPO_ROOT does not look like a SURE-EVAL repository root: $REPO_ROOT"
  echo "Hint: set REPO_ROOT explicitly when materializing this script."
  exit 1
fi

MODEL_NAME="${MODEL_NAME:-my_model}"
if [[ -n "${SURE_APPROVED_MODEL_ROOT:-}" ]]; then
  APPROVED_MODEL_ROOT="$SURE_APPROVED_MODEL_ROOT"
elif [[ -n "${SURE_EVAL_APPROVED_MODEL_DIR:-}" ]]; then
  APPROVED_MODEL_ROOT="$(dirname "$SURE_EVAL_APPROVED_MODEL_DIR")"
else
  HARNESS_ROOT="$(cd "$REPO_ROOT/../../.." && pwd)"
  APPROVED_MODEL_ROOT="$(node --import tsx "$HARNESS_ROOT/scripts/sure-site-value.mjs" storage.approved_models_roots | head -n 1)"
fi
SHARED_MODEL_ROOT="$APPROVED_MODEL_ROOT"
REPO_MODEL_ROOT="$APPROVED_MODEL_ROOT"
# The trusted container launcher injects the read-only mount target. Direct host
# execution keeps using the canonical approved NFS root.
APPROVED_MODEL_DIR="${SURE_EVAL_APPROVED_MODEL_DIR:-$APPROVED_MODEL_ROOT/$MODEL_NAME}"
if [[ ! -d "${MODEL_DIR:-}" ]]; then
  MODEL_DIR="$APPROVED_MODEL_DIR"
fi
if [[ ! -d "$MODEL_DIR" || "$(readlink -f "$MODEL_DIR")" != "$(readlink -f "$APPROVED_MODEL_DIR")" ]]; then
  echo "ERROR: MODEL_DIR must be the approved NFS model: $APPROVED_MODEL_DIR"
  exit 1
fi
MODEL_DIR="$(readlink -f "$APPROVED_MODEL_DIR")"
MODEL_DIR_SOURCE="approved_nfs_models"
PROTOCOL_ID="${PROTOCOL_ID:-standard_system}"
if [[ "$PROTOCOL_ID" != "standard_system" && "$PROTOCOL_ID" != "strict_core" ]]; then
  echo "ERROR: PROTOCOL_ID must be standard_system or strict_core"
  exit 1
fi
RUN_ID="${RUN_ID:-main_agent_${MODEL_NAME}_$(date +%Y%m%d_%H%M%S)}"
HARNESS_REPO_ROOT="$(cd "$REPO_ROOT/../../.." && pwd)"
EXPECTED_RUN_ROOT="$HARNESS_REPO_ROOT/sure/results/$MODEL_NAME/$PROTOCOL_ID"
RUN_DIR="${RUN_DIR:-$EXPECTED_RUN_ROOT/$RUN_ID}"
APPROVED_CONTAINER_RESULT_DIR="${SURE_EVAL_APPROVED_RESULT_DIR:-}"
if [[ "$(readlink -m "$RUN_DIR")" != "$(readlink -m "$EXPECTED_RUN_ROOT")/"* \
  && ( -z "$APPROVED_CONTAINER_RESULT_DIR" || "$(readlink -m "$RUN_DIR")" != "$(readlink -m "$APPROVED_CONTAINER_RESULT_DIR")" ) ]]; then
  echo "ERROR: RUN_DIR must stay under local staging root or equal the approved container result mount"
  exit 1
fi
MODEL_PYTHON="${MODEL_PYTHON:-${PYTHON_BIN:-python}}"
PYTHON_BIN="$MODEL_PYTHON"
: "${HARNESS_PYTHON_BIN:?HARNESS_PYTHON_BIN must be injected from the approved common Harness Runtime}"
_harness_python_works() {
  "$1" - <<'PY' >/dev/null 2>&1
import pydantic
import pydantic_settings
import rich
import structlog
import typer
import yaml
PY
}
if ! _harness_python_works "$HARNESS_PYTHON_BIN"; then
  echo "ERROR: HARNESS_PYTHON_BIN cannot import required harness modules: $HARNESS_PYTHON_BIN"
  echo "Repair the locked common Harness Runtime before retrying; Eval must not install into Model Python."
  exit 1
fi
if [[ "$(readlink -f "$HARNESS_PYTHON_BIN")" == "$(readlink -f "$(command -v "$MODEL_PYTHON")")" ]]; then
  echo "ERROR: HARNESS_PYTHON_BIN and MODEL_PYTHON resolved to the same executable"
  exit 1
fi
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
HARNESS_PYTHON_VERSION="$("$HARNESS_PYTHON_BIN" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
REPO_DEPS_TARGET="${REPO_DEPS_TARGET:-$RUN_DIR/.runtime/repo_deps/python$HARNESS_PYTHON_VERSION}"
MAX_SAMPLES="${MAX_SAMPLES:-0}"
SKIP_VALIDATE_AND_EVAL="${SKIP_VALIDATE_AND_EVAL:-0}"
NO_RESUME="${NO_RESUME:-0}"
LANGUAGE="${LANGUAGE:-}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$RUN_DIR/.runtime/cache/huggingface/hub}"
export HARNESS_PYTHON_BIN
export SURE_EVAL_HARNESS_PYTHON_BIN="$HARNESS_PYTHON_BIN"
export MODEL_PYTHON
if [[ -n "${SERVER_SCRIPT_OVERRIDE:-}" ]]; then
  export SURE_EVAL_SERVER_SCRIPT_OVERRIDE="$SERVER_SCRIPT_OVERRIDE"
fi
export PYTHONPATH="${REPO_DEPS_TARGET}:${REPO_ROOT}/scripts:${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
RESULTS_DIR="${RESULTS_DIR:-$RUN_DIR/results}"
METRICS="${METRICS:-}"
export SURE_EVAL_METRICS="$METRICS"
EVALUATION_BACKEND="${EVALUATION_BACKEND:-external}"
STRICT_MAIN_FLOW="${STRICT_MAIN_FLOW:-1}"
EVALUATION_TIMEOUT="${EVALUATION_TIMEOUT:-7200}"
AUDIO_EVAL_TASKS="${AUDIO_EVAL_TASKS:-TTS VC}"
REPAIR_INVALID_ONLY="${REPAIR_INVALID_ONLY:-0}"
REPAIR_VALIDATION_PAYLOAD="${REPAIR_VALIDATION_PAYLOAD:-$RUN_DIR/validation_payload.json}"
EXECUTION_PATH="${SURE_EVAL_EXECUTION_PATH:-${EXECUTION_PATH:-unknown}}"

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

# ---------------------------------------------------------------------------
# Device resolution: explicit override > gpu_preflight recommendation > config default
# ---------------------------------------------------------------------------
DEVICE="${DEVICE:-${DEVICE_RESOLVED:-${SURE_EVAL_DEVICE:-}}}"
if [[ -z "$DEVICE" && -f "$RUN_DIR/execution_readiness_report.json" ]]; then
  RECOMMENDED_DEVICE=$(python -c "
import json,sys
try:
    data=json.load(open('$RUN_DIR/execution_readiness_report.json'))
    print(data.get('gpu_preflight',{}).get('recommended_device',''))
except:
    pass
" 2>/dev/null)
  if [[ -n "$RECOMMENDED_DEVICE" ]]; then
    DEVICE="$RECOMMENDED_DEVICE"
    echo "Auto-selected DEVICE from gpu_preflight: $DEVICE"
  fi
fi
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
export SURE_EVAL_DEVICE_REQUEST="$DEVICE_REQUEST"
export SURE_EVAL_DEVICE_ACTUAL="$DEVICE_ACTUAL"
export SURE_EVAL_EXECUTION_PATH="$EXECUTION_PATH"

# ---------------------------------------------------------------------------
# Dataset handling: support single or multiple datasets
# ---------------------------------------------------------------------------
# Accepts space-separated dataset names via DATASETS.
# For backward compatibility, falls back to DATASET if DATASETS is unset.
if [[ -z "${DATASETS:-}" ]]; then
  DATASETS="${DATASET:-}"
fi
if [[ -z "$DATASETS" ]]; then
  echo "ERROR: No datasets specified. Set DATASETS to one or more source roots allowed by the active site policy."
  exit 1
fi
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
echo "Model dir: $MODEL_DIR ($MODEL_DIR_SOURCE)"
echo "Datasets: ${DATASET_ARRAY[*]}"
echo "Harness Python: $HARNESS_PYTHON_BIN"
echo "Model Python: $MODEL_PYTHON"
echo "Execution path: $EXECUTION_PATH"
echo "DEVICE_REQUEST: ${DEVICE_REQUEST:-auto/from-runtime}"
echo "DEVICE_ACTUAL: ${DEVICE_ACTUAL:-auto/from-runtime}"
echo "CUDA_VISIBLE_DEVICES: ${CUDA_VISIBLE_DEVICES-<unset>}"
echo "MAX_SAMPLES: $MAX_SAMPLES"
echo "SKIP_VALIDATE_AND_EVAL: $SKIP_VALIDATE_AND_EVAL"
echo "EVALUATION_TIMEOUT: $EVALUATION_TIMEOUT"
echo "========================================"

# ---------------------------------------------------------------------------
# [1/5] Prepare datasets
# ---------------------------------------------------------------------------
echo "[1/5] Preparing datasets..."
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/prepare_sure_dataset.py" \
  --dataset "${DATASET_ARRAY[@]}" \
  --output "$RUN_DIR/prepare_summary.json"

mapfile -t EXPANDED_DATASET_ARRAY < <("$HARNESS_PYTHON_BIN" - "$RUN_DIR/prepare_summary.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
for item in payload.get("prepared", []):
    dataset = item.get("dataset")
    if dataset:
        print(dataset)
PY
)
if [[ ${#EXPANDED_DATASET_ARRAY[@]} -eq 0 ]]; then
  echo "ERROR: Dataset preparation did not return any concrete dataset splits."
  exit 1
fi
DATASET_ARRAY=("${EXPANDED_DATASET_ARRAY[@]}")
echo "Concrete datasets: ${DATASET_ARRAY[*]}"

# [2.6/5] Evaluation readiness gate
# The locked Evaluation Runtime used to be exercised for the first time in
# [5/5], with every prediction already generated. Two runs lost 4h51m and
# 1h21m of GPU to a runtime that was never going to load. Ask before the pass.
case "$TOOL_NAME" in
  synthesize_speech|convert_voice)
    : # Audio tasks hand off at [4/5] and never reach the evaluation engine.
    ;;
  *)
    if [[ "$SKIP_VALIDATE_AND_EVAL" != "1" && "$EVALUATION_BACKEND" == "external" ]]; then
      EVAL_ENGINE_ROOT="${SURE_EVALUATION_HOME:-$HARNESS_REPO_ROOT/sure/external/sure-evaluation}"
      echo "[2.6/5] evaluation readiness ($EVAL_ENGINE_ROOT)"
      READINESS_EXIT=0
      "$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/evaluation_runtime.py" \
        --engine-root "$EVAL_ENGINE_ROOT" \
        --output "$RUN_DIR/evaluation_readiness.json" >/dev/null || READINESS_EXIT=$?
      if [[ "$READINESS_EXIT" != "0" ]]; then
        echo "ERROR: the evaluation runtime is not usable; [5/5] would fail after the full prediction pass." >&2
        echo "Run directory: $RUN_DIR" >&2
        exit "$READINESS_EXIT"
      fi
      echo "Evaluation runtime ready: $RUN_DIR/evaluation_readiness.json"
    fi
    ;;
esac

if [[ "$REPAIR_INVALID_ONLY" == "1" ]]; then
  # -------------------------------------------------------------------------
  # [2R/5] Repair invalid prediction rows only
  # -------------------------------------------------------------------------
  echo "[2R/5] Planning targeted invalid-row prediction repair..."
  REPAIR_DATASETS_FILE="$RUN_DIR/prediction_repair_datasets.txt"
  REPAIR_PLAN_FILE="$RUN_DIR/prediction_repair_plan.json"
  "$HARNESS_PYTHON_BIN" - "$REPAIR_VALIDATION_PAYLOAD" "$RUN_DIR" "$REPAIR_DATASETS_FILE" "$REPAIR_PLAN_FILE" <<'PY'
import json
import sys
from pathlib import Path

validation_path = Path(sys.argv[1])
run_dir = Path(sys.argv[2])
datasets_file = Path(sys.argv[3])
plan_file = Path(sys.argv[4])

if not validation_path.exists():
    raise FileNotFoundError(f"Validation payload not found: {validation_path}")

payload = json.loads(validation_path.read_text(encoding="utf-8"))
repair_plan = []
for result in payload.get("results") or []:
    dataset = result.get("dataset")
    invalid_keys = sorted(
        set(result.get("empty_prediction_keys") or [])
        | set(result.get("contract_violation_keys") or [])
    )
    if dataset and invalid_keys:
        repair_plan.append({"dataset": dataset, "keys": invalid_keys})

pred_dir = run_dir / "predictions"
for item in repair_plan:
    dataset = item["dataset"]
    invalid = set(item["keys"])
    txt_path = pred_dir / f"{dataset}.txt"
    jsonl_path = pred_dir / f"{dataset}.jsonl"

    if txt_path.exists():
        kept = []
        for raw in txt_path.read_text(encoding="utf-8").splitlines():
            key = raw.split("\t", 1)[0].split(None, 1)[0] if raw.strip() else ""
            if key not in invalid:
                kept.append(raw)
        txt_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")

    if jsonl_path.exists():
        kept_rows = []
        for raw in jsonl_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                kept_rows.append(raw)
                continue
            if str(row.get("key", "")) not in invalid:
                kept_rows.append(raw)
        jsonl_path.write_text("\n".join(kept_rows) + ("\n" if kept_rows else ""), encoding="utf-8")

datasets_file.write_text("\n".join(item["dataset"] for item in repair_plan) + ("\n" if repair_plan else ""), encoding="utf-8")
plan_file.write_text(json.dumps({"repair_plan": repair_plan}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({"repair_plan": repair_plan}, indent=2, ensure_ascii=False))
PY

  if [[ ! -s "$REPAIR_DATASETS_FILE" ]]; then
    echo "No invalid prediction rows found in $REPAIR_VALIDATION_PAYLOAD; continuing to validation."
  else
    echo "[3R/5] Regenerating repaired rows with resume..."
    mapfile -t REPAIR_DATASET_ARRAY < "$REPAIR_DATASETS_FILE"
    for dataset in "${REPAIR_DATASET_ARRAY[@]}"; do
      [[ -z "$dataset" ]] && continue
      echo "  -> Repairing predictions for $dataset"
      GEN_ARGS=(
        --model-dir "$MODEL_DIR"
        --dataset "$dataset"
        --run-dir "$RUN_DIR"
        --tool-name "$TOOL_NAME"
        --resume
      )
      if [[ -n "$LANGUAGE" ]]; then
        GEN_ARGS+=(--language "$LANGUAGE")
      fi
      if [[ -n "$DEVICE" ]]; then
        GEN_ARGS+=(--device "$DEVICE")
      fi
      "$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" "${GEN_ARGS[@]}"
    done
  fi
else
  # ---------------------------------------------------------------------------
  # [2/5] Materialize prediction templates
  # ---------------------------------------------------------------------------
  echo "[2/5] Materializing prediction templates..."
  "$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/materialize_predictions_template.py" \
    --dataset "${DATASET_ARRAY[@]}" \
    --output-dir "$RUN_DIR/predictions" \
    --manifest-name manifest.json \
    --overwrite

  # ---------------------------------------------------------------------------
  # [2.5/5] Smoke test gate (first dataset only)
  # ---------------------------------------------------------------------------
  SMOKE_TEST_DATASET="${DATASET_ARRAY[0]}"
  if [[ -z "${SMOKE_TEST_SAMPLES:-}" ]]; then
    if [[ "$MAX_SAMPLES" =~ ^[0-9]+$ && "$MAX_SAMPLES" != "0" && "$MAX_SAMPLES" -lt 10 ]]; then
      SMOKE_TEST_SAMPLES="$MAX_SAMPLES"
    else
      SMOKE_TEST_SAMPLES="10"
    fi
  fi
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
  "$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" "${SMOKE_ARGS[@]}"

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

  if [[ "${SMOKE_ONLY:-0}" == "1" ]]; then
    echo "Smoke-only mode requested; stopping after smoke gate."
    exit 0
  fi

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
    "$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/generate_predictions_via_server.py" "${GEN_ARGS[@]}"
  done
fi

# ---------------------------------------------------------------------------
# [3.5/5] Write prediction generation status summary
# ---------------------------------------------------------------------------
echo "Writing prediction generation status..."
PRED_STATUS_FILE="$RUN_DIR/prediction_generation_status.json"
if [[ ! -f "$PRED_STATUS_FILE" ]]; then
cat > "$PRED_STATUS_FILE" <<EOF
{
  "schema": "sure.eval.prediction_generation_status.v2",
  "run_id": "$RUN_ID",
  "model_name": "$MODEL_NAME",
  "model_dir": "$MODEL_DIR",
  "model_dir_source": "$MODEL_DIR_SOURCE",
  "shared_model_root": "$SHARED_MODEL_ROOT",
  "repo_model_root": "$REPO_MODEL_ROOT",
  "execution_path": "$EXECUTION_PATH",
  "execution_requested": "${SURE_EVAL_EXECUTION_REQUESTED:-}",
  "execution_job_id": "${SURE_EVAL_EXECUTION_JOB_ID:-}",
  "inference_call_mode": "direct_server_use",
  "tool_name": "$TOOL_NAME",
  "device_request": "${SURE_EVAL_DEVICE_REQUEST:-${DEVICE:-}}",
  "device_actual": "${SURE_EVAL_DEVICE_ACTUAL:-${DEVICE:-}}",
  "cuda_visible_devices": "${CUDA_VISIBLE_DEVICES:-}",
  "datasets": [
EOF

FIRST=1
for dataset in "${DATASET_ARRAY[@]}"; do
  PRED_FILE="$RUN_DIR/predictions/${dataset}.txt"
  NUM_LINES=0
  if [[ -f "$PRED_FILE" ]]; then
    # Not "wc -l": the snapshot writer materializes a row for every sample the
    # moment it first runs, so the line count is the dataset size from the first
    # second onward and says nothing about how much was generated. A row counts
    # once it has a prediction in the second field, the same test the smoke gate
    # uses.
    NUM_LINES=$(awk -F'\t' '$2!="" {n++} END {print n+0}' "$PRED_FILE" || echo 0)
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
fi

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
VALIDATE_ARGS=(
  --dataset "${DATASET_ARRAY[@]}"
  --pred-dir "$RUN_DIR/predictions"
  --require-nonempty
  --output "$RUN_DIR/validation_payload.json"
)
if [[ "$MAX_SAMPLES" != "0" ]]; then
  VALIDATE_ARGS+=(--max-samples "$MAX_SAMPLES")
fi
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/validate_prediction_files.py" "${VALIDATE_ARGS[@]}"

AUDIO_SPLIT_FILE="$RUN_DIR/audio_evaluation_dataset_split.json"
"$HARNESS_PYTHON_BIN" - "$RUN_DIR/validation_payload.json" "$AUDIO_EVAL_TASKS" "$AUDIO_SPLIT_FILE" "$SURE_EVAL_CONFIG" <<'PY'
import json
import sys
from pathlib import Path
import yaml

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
audio_tasks = {item.upper() for item in sys.argv[2].split()}
output = Path(sys.argv[3])
config = yaml.safe_load(Path(sys.argv[4]).read_text(encoding="utf-8")) or {}
datasets_root = Path((config.get("data") or {}).get("datasets") or "data/datasets")
datasets = [item.get("dataset") for item in payload.get("results") or [] if item.get("dataset")]
audio_datasets = []
non_audio_datasets = []
tasks_by_dataset = {}
for dataset in datasets:
    path = datasets_root / "sure_benchmark" / "jsonl" / f"{dataset}.jsonl"
    task = ""
    if not path.exists():
        non_audio_datasets.append(dataset)
        continue
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        task = str(sample.get("task", "")).upper()
        break
    tasks_by_dataset[dataset] = task
    if task in audio_tasks:
        audio_datasets.append(dataset)
    else:
        non_audio_datasets.append(dataset)
output.write_text(json.dumps({
    "audio_tasks": sorted(audio_tasks),
    "audio_datasets": audio_datasets,
    "non_audio_datasets": non_audio_datasets,
    "tasks_by_dataset": tasks_by_dataset,
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
mapfile -t AUDIO_DATASET_ARRAY < <("$HARNESS_PYTHON_BIN" - "$AUDIO_SPLIT_FILE" <<'PY'
import json
import sys
for item in json.load(open(sys.argv[1], encoding="utf-8")).get("audio_datasets", []):
    print(item)
PY
)
mapfile -t NON_AUDIO_DATASET_ARRAY < <("$HARNESS_PYTHON_BIN" - "$AUDIO_SPLIT_FILE" <<'PY'
import json
import sys
for item in json.load(open(sys.argv[1], encoding="utf-8")).get("non_audio_datasets", []):
    print(item)
PY
)

if [[ ${#AUDIO_DATASET_ARRAY[@]} -gt 0 ]]; then
  AUDIO_EVAL_METRICS="$METRICS"
  if [[ -z "$AUDIO_EVAL_METRICS" ]]; then
    AUDIO_EVAL_METRICS=$("$HARNESS_PYTHON_BIN" - "$AUDIO_SPLIT_FILE" "$SURE_EVAL_CONFIG" "$REPO_ROOT" <<'PY'
import json
import sys
from pathlib import Path
import yaml

split = json.load(open(sys.argv[1], encoding="utf-8"))
config = yaml.safe_load(Path(sys.argv[2]).read_text(encoding="utf-8")) or {}
repo_root = Path(sys.argv[3])
sys.path.insert(0, str(repo_root / "scripts"))
from evaluation_capabilities import supported_metrics_for_task_language
from resolve_evaluation_engine import resolve_engine_root

engine = resolve_engine_root(None)
datasets_root = Path((config.get("data") or {}).get("datasets") or "data/datasets")
metrics = []
for dataset in split.get("audio_datasets", []):
    path = datasets_root / "sure_benchmark" / "jsonl" / f"{dataset}.jsonl"
    if not path.exists():
        continue
    for line in path.read_text(encoding="utf-8").splitlines():
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
  "datasets": "$(printf '%s ' "${AUDIO_DATASET_ARRAY[@]}" | sed 's/ $//')",
  "metrics": "$AUDIO_EVAL_METRICS",
  "device": "${DEVICE:-}",
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
  echo "TTS/VC datasets are intentionally not evaluated in the model inference surface: ${AUDIO_DATASET_ARRAY[*]}"
  echo "Next template: scripts/templates/run_audio_evaluation_only.sh"
  if [[ ${#NON_AUDIO_DATASET_ARRAY[@]} -eq 0 ]]; then
    exit 0
  fi
fi

if [[ ${#NON_AUDIO_DATASET_ARRAY[@]} -gt 0 ]]; then
  DATASET_ARRAY=("${NON_AUDIO_DATASET_ARRAY[@]}")
  echo "Continuing in-surface evaluation for non-audio datasets only: ${DATASET_ARRAY[*]}"
fi

# ---------------------------------------------------------------------------
# [5/5] Evaluate predictions
# ---------------------------------------------------------------------------
EVAL_EXIT=0
echo "[5/5] Evaluating predictions..."
EVAL_ARGS=(
  --dataset "${DATASET_ARRAY[@]}"
  --pred-dir "$RUN_DIR/predictions"
  --tool-name "$MODEL_NAME"
  --run-dir "$RUN_DIR"
  --validation-payload "$RUN_DIR/validation_payload.json"
  --record
  --output "$RUN_DIR/evaluation_payload.json"
)
if [[ -n "$METRICS" ]]; then
  read -r -a METRIC_ARRAY <<< "$METRICS"
  for metric in "${METRIC_ARRAY[@]}"; do
    EVAL_ARGS+=(--metric "$metric")
  done
fi
if [[ -n "$DEVICE" ]]; then
  EVAL_ARGS+=(--device "$DEVICE")
fi
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/evaluate_predictions.py" \
  --results-dir "$RESULTS_DIR" \
  --protocol-id "$PROTOCOL_ID" \
  --model-dir "$MODEL_DIR" \
  --evaluation-backend "$EVALUATION_BACKEND" \
  --evaluation-timeout "$EVALUATION_TIMEOUT" \
  "${EVAL_ARGS[@]}" || EVAL_EXIT=$?

if [[ "$EVAL_EXIT" != "0" ]]; then
  echo "ERROR: Evaluation exited with code $EVAL_EXIT" >&2
  echo "Run directory: $RUN_DIR" >&2
  echo "Check predictions and logs before deciding next step." >&2
  exit "$EVAL_EXIT"
fi

# ---------------------------------------------------------------------------
# [6/5] Generate user-facing report snapshot
# ---------------------------------------------------------------------------
echo "[6/5] Generating report snapshot..."
"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/generate_report_snapshot.py" \
  --run-dir "$RUN_DIR" \
  --output "$RUN_DIR/report_snapshot.md" || echo "WARNING: Report snapshot generation failed (non-fatal)"

if [[ -f "$RUN_DIR/report_snapshot.md" ]]; then
  mkdir -p "$RESULTS_DIR"
  cp "$RUN_DIR/report_snapshot.md" "$RESULTS_DIR/report_snapshot.md"
fi

"$HARNESS_PYTHON_BIN" "$REPO_ROOT/scripts/finalize_result_bundle.py" \
  --run-dir "$RUN_DIR" \
  --published-run-dir "${SURE_EVAL_PUBLISHED_RUN_DIR:-$RUN_DIR}"

echo "========================================"
echo "Run completed: $RUN_ID"
echo "Results: $RUN_DIR/evaluation_payload.json"
echo "Run-local report: $RUN_DIR/report.jsonl"
echo "Run-local protocol: $RUN_DIR/protocol.yaml"
echo "Report snapshot: $RUN_DIR/report_snapshot.md"
echo "========================================"
