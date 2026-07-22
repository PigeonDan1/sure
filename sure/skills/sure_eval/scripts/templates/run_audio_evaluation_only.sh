#!/usr/bin/env bash
set -euo pipefail

# SURE-EVAL TTS/VC audio evaluation-only entrypoint.
# This template consumes already validated prediction artifacts and must be run
# through src/sure_eval/evaluation node-local uv environments. The vc image is
# only the base runtime/interpreter surface, not the metric dependency source.

unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy

REPO_ROOT="${REPO_ROOT:-/workspace/sure-eval}"
MODEL_NAME="${MODEL_NAME:-my_model}"
RUN_ID="${RUN_ID:-main_agent_${MODEL_NAME}_audio_eval}"
SHARED_MODEL_ROOT="${SHARED_MODEL_ROOT:-/hpc_stor03/sjtu_home/jing.peng/workspace/model}"
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
RUN_DIR="${RUN_DIR:-$MODEL_DIR/eval_runs/$RUN_ID}"
DATASETS="${DATASETS:-${DATASET:-}}"
METRICS="${METRICS:-}"
RESULTS_DIR="${RESULTS_DIR:-$RUN_DIR/results}"
PROTOCOL_ID="${PROTOCOL_ID:-strict_core}"
PYTHON_BIN="${PYTHON_BIN:-python}"
HARNESS_PYTHON_BIN="${HARNESS_PYTHON_BIN:-$PYTHON_BIN}"
PROBE_ONLY="${PROBE_ONLY:-0}"
PROBE_TRANSCRIBE="${PROBE_TRANSCRIBE:-0}"
DEVICE="${DEVICE:-${DEVICE_RESOLVED:-${SURE_EVAL_DEVICE:-cuda:0}}}"
TOOL_NAME="${TOOL_NAME:-$MODEL_NAME}"
AUDIO_EVAL_MODE="${AUDIO_EVAL_MODE:-full}"
AUDIO_EVAL_SEGMENT="${AUDIO_EVAL_SEGMENT:-}"
SEGMENT_PAYLOADS="${SEGMENT_PAYLOADS:-}"
EVALUATION_TIMEOUT="${EVALUATION_TIMEOUT:-7200}"
PYTHON_BIN="$HARNESS_PYTHON_BIN"
PYVER="$("$PYTHON_BIN" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
REPO_DEPS_TARGET="${REPO_DEPS_TARGET:-$RUN_DIR/.runtime/repo_deps/python$PYVER}"
export HARNESS_PYTHON_BIN
export PYTHON_BIN
export REPO_DEPS_TARGET

if [[ -z "$DATASETS" ]]; then
  echo "ERROR: DATASETS or DATASET is required."
  exit 2
fi

cd "$REPO_ROOT"

if [[ ! -d "$REPO_ROOT/scripts" ]]; then
  echo "ERROR: REPO_ROOT does not look like a SURE-EVAL repository root: $REPO_ROOT"
  exit 2
fi
if [[ ! -d "$RUN_DIR" ]]; then
  echo "ERROR: RUN_DIR not found: $RUN_DIR"
  exit 2
fi

export PYTHONPATH="$REPO_DEPS_TARGET:$REPO_ROOT/scripts:$REPO_ROOT/src${PYTHONPATH:+:${PYTHONPATH}}"

ensure_python_pip() {
  local python_bin="$1"
  if "$python_bin" -m pip --version >/dev/null 2>&1; then
    return 0
  fi
  echo "Python runtime has no pip; bootstrapping with ensurepip: $python_bin"
  if "$python_bin" -m ensurepip --upgrade >/dev/null 2>&1 \
    && "$python_bin" -m pip --version >/dev/null 2>&1; then
    return 0
  fi
  echo "ERROR: Python runtime cannot install repository dependencies because pip is unavailable: $python_bin"
  echo "Hint: recreate the venv with pip/seed enabled, or run: $python_bin -m ensurepip --upgrade"
  exit 2
}

if [[ "$AUDIO_EVAL_SEGMENT" == "segment_tts_mos_utmos" && -z "${SURE_EVAL_NODE_LOCAL_PYTHON_SCORING_UTMOS:-}" ]]; then
  if [[ ! -x "/usr/bin/python3.8" ]]; then
    UTMOS_NODE_PYTHON="$(command -v "$PYTHON_BIN" 2>/dev/null || true)"
    if [[ -n "$UTMOS_NODE_PYTHON" ]]; then
      export SURE_EVAL_NODE_LOCAL_PYTHON_SCORING_UTMOS="$UTMOS_NODE_PYTHON"
    fi
  fi
fi
if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import pydantic
import pydantic_settings
import structlog
import yaml
import rich
import typer
import click
PY
then
  ensure_python_pip "$PYTHON_BIN"
  echo "Installing missing repository runtime dependencies into $REPO_DEPS_TARGET..."
  rm -rf "$REPO_DEPS_TARGET"
  "$PYTHON_BIN" -m pip install --retries 10 --timeout 60 --target "$REPO_DEPS_TARGET" \
    'pydantic==2.10.6' \
    'pydantic-settings==2.7.1' \
    'structlog==25.5.0' \
    'PyYAML==6.0.2' \
    'rich>=13.0' \
    'typer>=0.12' \
    'click>=8.0' \
    'imageio-ffmpeg==0.6.0'
fi
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
  "$PYTHON_BIN" - "$BASE_SURE_EVAL_CONFIG" "$SURE_EVAL_CONFIG" "$HARNESS_REPO_ROOT" "$RUN_DIR" "$SURE_EVAL_DATASETS_ROOT" <<'PY'
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
export SURE_TTS_AUDIO_RUNTIME="${SURE_TTS_AUDIO_RUNTIME:-node_local}"
export SURE_EVAL_MINIMAL_DATASET_MANAGER="${SURE_EVAL_MINIMAL_DATASET_MANAGER:-1}"
EVALUATION_BACKEND="${EVALUATION_BACKEND:-external}"
STRICT_MAIN_FLOW="${STRICT_MAIN_FLOW:-1}"

read -ra DATASET_ARRAY <<< "$DATASETS"
read -ra METRIC_ARRAY <<< "$METRICS"
if [[ ${#METRIC_ARRAY[@]} -eq 0 ]]; then
  mapfile -t METRIC_ARRAY < <("$PYTHON_BIN" - "$SURE_EVAL_CONFIG" "$REPO_ROOT" "${DATASET_ARRAY[@]}" <<'PY'
import json
import sys
from pathlib import Path
import yaml

metrics = []
config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
repo_root = Path(sys.argv[2])
sys.path.insert(0, str(repo_root / "scripts"))
from evaluation_capabilities import supported_metrics_for_task_language
from resolve_evaluation_engine import resolve_engine_root

engine = resolve_engine_root(None)
datasets_root = Path((config.get("data") or {}).get("datasets") or "data/datasets")
for dataset in sys.argv[3:]:
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
for metric in deduped:
    print(metric)
PY
)
fi
METRICS="$(printf '%s ' "${METRIC_ARRAY[@]}" | sed 's/ $//')"

segment_tts_semantic() {
  local selected=()
  for metric in "${METRIC_ARRAY[@]}"; do
    case "$metric" in
      tts_wer|tts_cer|vc_wer|vc_cer) selected+=("$metric") ;;
    esac
  done
  printf '%s\n' "${selected[@]}"
}

segment_tts_speaker_wavlm_ecapa() {
  local selected=()
  for metric in "${METRIC_ARRAY[@]}"; do
    case "$metric" in
      sim/wavlm-large|sim/ecapa-tdnn) selected+=("$metric") ;;
    esac
  done
  printf '%s\n' "${selected[@]}"
}

segment_tts_speaker_eres2net() {
  local selected=()
  for metric in "${METRIC_ARRAY[@]}"; do
    case "$metric" in
      sim/eres2net) selected+=("$metric") ;;
    esac
  done
  printf '%s\n' "${selected[@]}"
}

segment_tts_mos_dnsmos() {
  local selected=()
  for metric in "${METRIC_ARRAY[@]}"; do
    case "$metric" in
      dnsmos) selected+=("$metric") ;;
    esac
  done
  printf '%s\n' "${selected[@]}"
}

segment_tts_mos_wvmos() {
  local selected=()
  for metric in "${METRIC_ARRAY[@]}"; do
    case "$metric" in
      wv-mos) selected+=("$metric") ;;
    esac
  done
  printf '%s\n' "${selected[@]}"
}

segment_tts_mos_utmos() {
  local selected=()
  for metric in "${METRIC_ARRAY[@]}"; do
    case "$metric" in
      utmos) selected+=("$metric") ;;
    esac
  done
  printf '%s\n' "${selected[@]}"
}

if [[ "$AUDIO_EVAL_MODE" == "segment" ]]; then
  if [[ -z "$AUDIO_EVAL_SEGMENT" ]]; then
    echo "ERROR: AUDIO_EVAL_SEGMENT is required when AUDIO_EVAL_MODE=segment."
    exit 2
  fi
  mapfile -t SEGMENT_METRIC_ARRAY < <("$AUDIO_EVAL_SEGMENT")
  if [[ ${#SEGMENT_METRIC_ARRAY[@]} -eq 0 ]]; then
    echo "ERROR: segment $AUDIO_EVAL_SEGMENT selected no metrics from: ${METRIC_ARRAY[*]}"
    exit 2
  fi
  METRIC_ARRAY=("${SEGMENT_METRIC_ARRAY[@]}")
  METRICS="$(printf '%s ' "${METRIC_ARRAY[@]}" | sed 's/ $//')"
  SEGMENT_DIR="$RUN_DIR/evaluation_segments/$AUDIO_EVAL_SEGMENT"
  RESULTS_DIR="$SEGMENT_DIR/results"
  mkdir -p "$SEGMENT_DIR"
elif [[ "$AUDIO_EVAL_MODE" == "merge" ]]; then
  read -ra SEGMENT_PAYLOAD_ARRAY <<< "$SEGMENT_PAYLOADS"
  if [[ ${#SEGMENT_PAYLOAD_ARRAY[@]} -eq 0 ]]; then
    mapfile -t SEGMENT_PAYLOAD_ARRAY < <(find "$RUN_DIR/evaluation_segments" -mindepth 2 -maxdepth 2 -name evaluation_payload.json -print | sort)
  fi
  if [[ ${#SEGMENT_PAYLOAD_ARRAY[@]} -eq 0 ]]; then
    echo "ERROR: no segment payloads found for merge."
    exit 2
  fi
fi

if [[ "$PROBE_TRANSCRIBE" == "auto" ]]; then
  PROBE_TRANSCRIBE="0"
  for metric in "${METRIC_ARRAY[@]}"; do
    case "$metric" in
      tts_wer|tts_cer|vc_wer|vc_cer) PROBE_TRANSCRIBE="1" ;;
    esac
  done
fi

echo "========================================"
echo "SURE-EVAL audio evaluation-only run"
echo "Run ID: $RUN_ID"
echo "Model: $MODEL_NAME"
echo "Model dir: $MODEL_DIR ($MODEL_DIR_SOURCE)"
echo "Run dir: $RUN_DIR"
echo "Datasets: ${DATASET_ARRAY[*]}"
echo "Metrics: ${METRIC_ARRAY[*]:-dataset default}"
echo "Audio eval mode: $AUDIO_EVAL_MODE"
echo "Audio eval segment: ${AUDIO_EVAL_SEGMENT:-N/A}"
echo "Evaluation timeout: $EVALUATION_TIMEOUT"
echo "Python: $("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
echo "Python version: $PYVER"
echo "========================================"

echo "[preflight] ffmpeg"
if ! command -v ffmpeg >/dev/null 2>&1; then
  IMAGEIO_FFMPEG_BIN="$("$PYTHON_BIN" - <<'PY' 2>/dev/null || true
try:
    import imageio_ffmpeg
except Exception:
    raise SystemExit(0)
print(imageio_ffmpeg.get_ffmpeg_exe())
PY
)"
  if [[ -n "$IMAGEIO_FFMPEG_BIN" && -x "$IMAGEIO_FFMPEG_BIN" ]]; then
    mkdir -p "$RUN_DIR/.runtime/bin"
    ln -sfn "$IMAGEIO_FFMPEG_BIN" "$RUN_DIR/.runtime/bin/ffmpeg"
    export PATH="$RUN_DIR/.runtime/bin:$PATH"
  fi
fi
if command -v ffmpeg >/dev/null 2>&1; then
  ffmpeg -version | head -n 1
else
  echo "WARNING: ffmpeg not found on PATH; continuing because wav-only datasets may not require it."
fi

echo "[preflight] external sure-evaluation engine"
read -r ENGINE_SMOKE_TASK ENGINE_SMOKE_LANGUAGE < <("$PYTHON_BIN" - "$SURE_EVAL_CONFIG" "${DATASET_ARRAY[0]}" <<'PY'
import json
import sys
from pathlib import Path
import yaml

config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
datasets_root = Path((config.get("data") or {}).get("datasets") or "data/datasets")
dataset_jsonl = datasets_root / "sure_benchmark" / "jsonl" / f"{sys.argv[2]}.jsonl"
task = "tts"
language = "zh"
if dataset_jsonl.exists():
    for line in dataset_jsonl.read_text(encoding="utf-8").splitlines():
        if line.strip():
            sample = json.loads(line)
            task = str(sample.get("task") or task).lower()
            language = str(sample.get("language") or language).lower()
            break
print(task, language)
PY
)
"$PYTHON_BIN" "$REPO_ROOT/scripts/resolve_evaluation_engine.py" \
  --smoke \
  --task "$ENGINE_SMOKE_TASK" \
  --language "$ENGINE_SMOKE_LANGUAGE" \
  --metric "${METRIC_ARRAY[0]:-}"

echo "[preflight] validated prediction artifacts"
"$PYTHON_BIN" - "$RUN_DIR" "${DATASET_ARRAY[@]}" <<'PY'
from pathlib import Path
import json
import sys

run_dir = Path(sys.argv[1])
datasets = sys.argv[2:]
validation_path = run_dir / "validation_payload.json"
validation = json.loads(validation_path.read_text(encoding="utf-8"))
if not validation.get("is_valid"):
    raise SystemExit(f"validation is not valid: {validation_path}")
for dataset in datasets:
    pred = run_dir / "predictions" / f"{dataset}.txt"
    structured = run_dir / "predictions" / f"{dataset}.jsonl"
    if not pred.exists() or not structured.exists():
        raise FileNotFoundError(f"missing predictions for {dataset}")
    rows = [line for line in pred.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit(f"empty prediction file: {pred}")
    print(dataset, "predictions", len(rows))
PY

if [[ "$PROBE_TRANSCRIBE" == "1" ]]; then
  echo "[preflight] one-sample semantic transcription"
  "$PYTHON_BIN" - "$RUN_DIR" "$DEVICE" "$REPO_ROOT" "${DATASET_ARRAY[@]}" -- "${METRIC_ARRAY[@]}" <<'PY'
import json
import os
from pathlib import Path
import sys
import yaml

run_dir = Path(sys.argv[1])
device = sys.argv[2]
repo_root = Path(sys.argv[3])
separator = sys.argv.index("--")
datasets = sys.argv[4:separator]
metrics = [item.lower() for item in sys.argv[separator + 1 :]]
sys.path.insert(0, str(repo_root / "scripts"))
from resolve_evaluation_engine import resolve_engine_root

engine = resolve_engine_root(None)
if engine is None:
    raise SystemExit("unable to resolve standalone sure-evaluation engine")
engine_root = engine[1]
sys.path.insert(0, str(engine_root / "src"))
from sure_eval.evaluation.cli_adapters import build_pipeline_spec

def _dataset_task_language(dataset: str) -> tuple[str, str]:
    config_path = os.environ.get("SURE_EVAL_CONFIG")
    datasets_root = Path("data/datasets")
    if config_path:
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        datasets_root = Path((config.get("data") or {}).get("datasets") or datasets_root)
    dataset_jsonl = datasets_root / "sure_benchmark" / "jsonl" / f"{dataset}.jsonl"
    task = "TTS"
    language = "en"
    if not dataset_jsonl.exists():
        return task, language
    for line in dataset_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        return str(sample.get("task", task)).lower(), str(sample.get("language", language))
    return task, language

def _metric_has_transcription_route(task: str, metric: str, language: str) -> bool:
    try:
        pipeline = build_pipeline_spec(task, language=language, metric=metric)
    except Exception:
        return False
    for node in pipeline.get("nodes") or []:
        if str(node.get("node_id") or "").startswith("transcription/"):
            return True
    return False

def _resolve_probe_audio_path(value: str) -> Path:
    audio = Path(value)
    if audio.is_absolute() and not audio.exists():
        workspace_root = Path("/workspace/sure-eval")
        try:
            relative_to_workspace = audio.relative_to(workspace_root)
        except ValueError:
            pass
        else:
            remapped = Path.cwd() / relative_to_workspace
            if remapped.exists():
                audio = remapped
    return audio

semantic_probe_cases = []
for dataset in datasets:
    task, language = _dataset_task_language(dataset)
    for metric in metrics:
        if _metric_has_transcription_route(task, metric, language):
            semantic_probe_cases.append({"dataset": dataset, "task": task, "language": language, "metric": metric})

if not semantic_probe_cases:
    raise SystemExit("PROBE_TRANSCRIBE=1 but no language-compatible semantic metric was selected")

from sure_eval.evaluation.audio_runtime import build_tts_runtime, build_vc_runtime

for case in semantic_probe_cases:
    dataset = case["dataset"]
    task = case["task"]
    language = case["language"]
    metric = case["metric"]
    prediction_file = run_dir / "predictions" / f"{dataset}.txt"
    first = prediction_file.read_text(encoding="utf-8").splitlines()[0]
    sample_id, audio_path = first.split("\t", 1)
    audio = _resolve_probe_audio_path(audio_path)
    if not audio.exists():
        raise FileNotFoundError(audio)
    build_runtime = build_vc_runtime if task == "vc" else build_tts_runtime
    runtime = build_runtime(metrics=(metric,), language=language, device=device, cache_dir=None)
    key = language if language in runtime["transcribers"] else next(iter(runtime["transcribers"]))
    runner = runtime["transcribers"][key]
    text = runner.transcribe(str(audio), language=language)
    node = getattr(runner, "node_id", type(runner).__name__)
    print("sample_id", sample_id)
    print("dataset", dataset)
    print("language", language)
    print("metric", metric)
    print("transcription_node", node)
    print("transcript", text[:240])
PY
fi

if [[ "$PROBE_ONLY" == "1" ]]; then
  echo "Probe complete; skipping full evaluation."
  exit 0
fi

if [[ "$AUDIO_EVAL_MODE" == "merge" ]]; then
  echo "[1/2] merging segmented evaluation payloads"
  MERGE_ARGS=(
    --dataset "${DATASET_ARRAY[@]}"
    --pred-dir "$RUN_DIR/predictions"
    --tool-name "$TOOL_NAME"
    --results-dir "$RESULTS_DIR"
    --protocol-id "$PROTOCOL_ID"
    --model-dir "$MODEL_DIR"
    --run-dir "$RUN_DIR"
    --validation-payload "$RUN_DIR/validation_payload.json"
    --evaluation-backend "$EVALUATION_BACKEND"
    --output "$RUN_DIR/evaluation_payload.json"
  )
  if [[ "$STRICT_MAIN_FLOW" == "1" ]]; then
    MERGE_ARGS+=(--strict-main-flow)
  fi
  for payload_path in "${SEGMENT_PAYLOAD_ARRAY[@]}"; do
    MERGE_ARGS+=(--merge-payload "$payload_path")
  done
  "$PYTHON_BIN" "$REPO_ROOT/scripts/evaluate_predictions.py" "${MERGE_ARGS[@]}"

  echo "[2/2] refreshing report snapshot"
  "$PYTHON_BIN" "$REPO_ROOT/scripts/generate_report_snapshot.py" \
    --run-dir "$RUN_DIR" \
    --output "$RUN_DIR/report_snapshot.md"

  if [[ -f "$RUN_DIR/report_snapshot.md" ]]; then
    mkdir -p "$RESULTS_DIR"
    cp "$RUN_DIR/report_snapshot.md" "$RESULTS_DIR/report_snapshot.md"
  fi

  "$PYTHON_BIN" - "$RUN_DIR" "$RESULTS_DIR" <<'PY'
from pathlib import Path
import json
import sys

run_dir = Path(sys.argv[1])
results_dir = Path(sys.argv[2])
payload = json.loads((run_dir / "evaluation_payload.json").read_text(encoding="utf-8"))
required = [
    run_dir / "evaluation_payload.json",
    run_dir / "report.jsonl",
    run_dir / "protocol.yaml",
    results_dir / "report.jsonl",
    results_dir / "protocol.yaml",
]
for result in payload.get("results", []):
    dataset = result["dataset"]
    metric = result["metric"].replace("/", "_").replace(" ", "_").lower()
    required.extend(
        [
            run_dir / "metrics" / dataset / metric / "report.json",
            run_dir / "metrics" / dataset / metric / "pipeline_description.json",
            run_dir / "sample_reports" / dataset / f"{metric}.jsonl",
        ]
    )
missing = [str(path) for path in required if not path.exists() or path.stat().st_size == 0]
if missing:
    raise SystemExit("missing required evaluation artifacts: " + ", ".join(missing))
for result in payload.get("results", []):
    metric_result = result.get("result") or {}
    artifacts = result.get("artifacts") or {}
    print("evaluation_score", result["dataset"], result["metric"], metric_result.get("score", result.get("score")))
    print("metric_artifact_dir", artifacts.get("metric_artifact_dir", result.get("metric_artifact_dir")))
PY

  cat > "$RUN_DIR/evaluation_only_status.json" <<EOF
{
  "run_id": "$RUN_ID",
  "status": "completed",
  "execution_surface": "audio_evaluation_only",
  "audio_eval_mode": "merge",
  "datasets": "$(printf '%s ' "${DATASET_ARRAY[@]}" | sed 's/ $//')",
  "metrics": "$(printf '%s ' "${METRIC_ARRAY[@]}" | sed 's/ $//')",
  "segment_payloads": "$(printf '%s ' "${SEGMENT_PAYLOAD_ARRAY[@]}" | sed 's/ $//')",
  "results_dir": "$RESULTS_DIR"
}
EOF

  echo "========================================"
  echo "Audio evaluation-only merge completed."
  echo "Evaluation payload: $RUN_DIR/evaluation_payload.json"
  echo "Run-local report: $RUN_DIR/report.jsonl"
  echo "Run-local protocol: $RUN_DIR/protocol.yaml"
  echo "Report snapshot: $RUN_DIR/report_snapshot.md"
  echo "========================================"
  exit 0
fi

echo "[1/2] evaluating existing predictions"
EVAL_ARGS=(
  --dataset "${DATASET_ARRAY[@]}"
  --pred-dir "$RUN_DIR/predictions"
  --tool-name "$TOOL_NAME"
  --results-dir "$RESULTS_DIR"
  --protocol-id "$PROTOCOL_ID"
  --model-dir "$MODEL_DIR"
  --run-dir "${SEGMENT_DIR:-$RUN_DIR}"
  --validation-payload "$RUN_DIR/validation_payload.json"
  --device "$DEVICE"
  --evaluation-backend "$EVALUATION_BACKEND"
  --evaluation-timeout "$EVALUATION_TIMEOUT"
  --record
  --output "${SEGMENT_DIR:-$RUN_DIR}/evaluation_payload.json"
)
if [[ "$STRICT_MAIN_FLOW" == "1" ]]; then
  EVAL_ARGS+=(--strict-main-flow)
fi
for metric in "${METRIC_ARRAY[@]}"; do
  EVAL_ARGS+=(--metric "$metric")
done
"$PYTHON_BIN" "$REPO_ROOT/scripts/evaluate_predictions.py" "${EVAL_ARGS[@]}"

if [[ "$AUDIO_EVAL_MODE" == "segment" ]]; then
  "$PYTHON_BIN" - "${SEGMENT_DIR:-$RUN_DIR}" <<'PY'
from pathlib import Path
import json
import sys

segment_dir = Path(sys.argv[1])
payload_path = segment_dir / "evaluation_payload.json"
payload = json.loads(payload_path.read_text(encoding="utf-8"))
required = [payload_path, segment_dir / "report.jsonl", segment_dir / "protocol.yaml"]
for result in payload.get("results", []):
    dataset = result["dataset"]
    metric = result["metric"].replace("/", "_").replace(" ", "_").lower()
    required.extend(
        [
            segment_dir / "metrics" / dataset / metric / "report.json",
            segment_dir / "metrics" / dataset / metric / "pipeline_description.json",
            segment_dir / "sample_reports" / dataset / f"{metric}.jsonl",
        ]
    )
missing = [str(path) for path in required if not path.exists() or path.stat().st_size == 0]
if missing:
    raise SystemExit("missing required segment artifacts: " + ", ".join(missing))
for result in payload.get("results", []):
    metric_result = result.get("result") or {}
    print("segment_evaluation_score", result["dataset"], result["metric"], metric_result.get("score", result.get("score")))
PY
  cat > "${SEGMENT_DIR:-$RUN_DIR}/evaluation_only_status.json" <<EOF
{
  "run_id": "$RUN_ID",
  "status": "completed",
  "execution_surface": "audio_evaluation_only",
  "audio_eval_mode": "segment",
  "audio_eval_segment": "$AUDIO_EVAL_SEGMENT",
  "datasets": "$(printf '%s ' "${DATASET_ARRAY[@]}" | sed 's/ $//')",
  "metrics": "$(printf '%s ' "${METRIC_ARRAY[@]}" | sed 's/ $//')",
  "segment_payload": "${SEGMENT_DIR:-$RUN_DIR}/evaluation_payload.json"
}
EOF
  echo "========================================"
  echo "Audio evaluation-only segment completed."
  echo "Segment: $AUDIO_EVAL_SEGMENT"
  echo "Segment payload: ${SEGMENT_DIR:-$RUN_DIR}/evaluation_payload.json"
  echo "========================================"
  exit 0
fi

echo "[2/2] refreshing report snapshot"
"$PYTHON_BIN" "$REPO_ROOT/scripts/generate_report_snapshot.py" \
  --run-dir "$RUN_DIR" \
  --output "$RUN_DIR/report_snapshot.md"

if [[ -f "$RUN_DIR/report_snapshot.md" ]]; then
  mkdir -p "$RESULTS_DIR"
  cp "$RUN_DIR/report_snapshot.md" "$RESULTS_DIR/report_snapshot.md"
fi

"$PYTHON_BIN" - "$RUN_DIR" "$RESULTS_DIR" <<'PY'
from pathlib import Path
import json
import sys

run_dir = Path(sys.argv[1])
results_dir = Path(sys.argv[2])
payload = json.loads((run_dir / "evaluation_payload.json").read_text(encoding="utf-8"))
required = [
    run_dir / "evaluation_payload.json",
    run_dir / "report.jsonl",
    run_dir / "protocol.yaml",
    results_dir / "report.jsonl",
    results_dir / "protocol.yaml",
]
for result in payload.get("results", []):
    dataset = result["dataset"]
    metric = result["metric"].replace("/", "_").replace(" ", "_").lower()
    required.extend(
        [
            run_dir / "metrics" / dataset / metric / "report.json",
            run_dir / "metrics" / dataset / metric / "pipeline_description.json",
            run_dir / "sample_reports" / dataset / f"{metric}.jsonl",
        ]
    )
missing = [str(path) for path in required if not path.exists() or path.stat().st_size == 0]
if missing:
    raise SystemExit("missing required evaluation artifacts: " + ", ".join(missing))
for result in payload.get("results", []):
    metric_result = result.get("result") or {}
    artifacts = result.get("artifacts") or {}
    print("evaluation_score", result["dataset"], result["metric"], metric_result.get("score", result.get("score")))
    print("metric_artifact_dir", artifacts.get("metric_artifact_dir", result.get("metric_artifact_dir")))
PY

cat > "$RUN_DIR/evaluation_only_status.json" <<EOF
{
  "run_id": "$RUN_ID",
  "status": "completed",
  "execution_surface": "audio_evaluation_only",
  "datasets": "$(printf '%s ' "${DATASET_ARRAY[@]}" | sed 's/ $//')",
  "metrics": "$(printf '%s ' "${METRIC_ARRAY[@]}" | sed 's/ $//')",
  "results_dir": "$RESULTS_DIR"
}
EOF

echo "========================================"
echo "Audio evaluation-only run completed."
echo "Evaluation payload: $RUN_DIR/evaluation_payload.json"
echo "Run-local report: $RUN_DIR/report.jsonl"
echo "Run-local protocol: $RUN_DIR/protocol.yaml"
echo "Report snapshot: $RUN_DIR/report_snapshot.md"
echo "========================================"
