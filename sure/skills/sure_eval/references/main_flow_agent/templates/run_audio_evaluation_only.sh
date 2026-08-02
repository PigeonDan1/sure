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
RUN_DIR="${RUN_DIR:-$MODEL_DIR/eval_runs/$RUN_ID}"
DATASETS="${DATASETS:-${DATASET:-}}"
METRICS="${METRICS:-}"
RESULTS_DIR="${RESULTS_DIR:-$REPO_ROOT/results/$MODEL_NAME/strict_core}"
PROTOCOL_ID="${PROTOCOL_ID:-strict_core}"
PYTHON_BIN="${PYTHON_BIN:-python}"
PROBE_ONLY="${PROBE_ONLY:-0}"
PROBE_TRANSCRIBE="${PROBE_TRANSCRIBE:-auto}"
DEVICE="${DEVICE:-cuda:0}"
TOOL_NAME="${TOOL_NAME:-$MODEL_NAME}"
AUDIO_EVAL_MODE="${AUDIO_EVAL_MODE:-full}"
AUDIO_EVAL_SEGMENT="${AUDIO_EVAL_SEGMENT:-}"
SEGMENT_PAYLOADS="${SEGMENT_PAYLOADS:-}"

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

PYVER="$("$PYTHON_BIN" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
export PYTHONPATH="$REPO_ROOT/src:${PYTHONPATH:-}"
export SURE_EVAL_CONFIG="${SURE_EVAL_CONFIG:-$REPO_ROOT/config/default.yaml}"
export SURE_TTS_AUDIO_RUNTIME="${SURE_TTS_AUDIO_RUNTIME:-node_local}"
export SURE_EVAL_MINIMAL_DATASET_MANAGER="${SURE_EVAL_MINIMAL_DATASET_MANAGER:-1}"

read -ra DATASET_ARRAY <<< "$DATASETS"
read -ra METRIC_ARRAY <<< "$METRICS"
if [[ ${#METRIC_ARRAY[@]} -eq 0 ]]; then
  mapfile -t METRIC_ARRAY < <("$PYTHON_BIN" - "${DATASET_ARRAY[@]}" <<'PY'
import json
import sys
from pathlib import Path

metrics = []
for dataset in sys.argv[1:]:
    path = Path("data/datasets/sure_benchmark/jsonl") / f"{dataset}.jsonl"
    if not path.exists():
        continue
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
echo "Python: $("$PYTHON_BIN" -c 'import sys; print(sys.executable)')"
echo "Python version: $PYVER"
echo "========================================"

echo "[preflight] ffmpeg"
command -v ffmpeg
ffmpeg -version | head -n 1

echo "[preflight] python imports and semantic cache"
"$PYTHON_BIN" - "$METRICS" "$SURE_TTS_AUDIO_RUNTIME" <<'PY'
from pathlib import Path
import importlib
import shutil
import sys

metrics = {item.strip().lower() for item in sys.argv[1].split() if item.strip()}
runtime = sys.argv[2].lower()
print("python", sys.version.replace("\n", " "))
print("ffmpeg", shutil.which("ffmpeg"))
for name in ("yaml",):
    module = importlib.import_module(name)
    print(name, getattr(module, "__version__", "imported"))
from sure_eval.evaluation.scripts import run_task
print("run_task", callable(run_task))
if runtime != "in_process":
    node_ids = []
    if metrics & {"tts_wer", "vc_wer"}:
        node_ids.append("transcription/whisper_large_v3")
    if metrics & {"tts_cer", "vc_cer"}:
        node_ids.append("transcription/paraformer_zh")
    if "sim/wavlm-large" in metrics:
        node_ids.append("scoring/wavlm_large_sim")
    if "sim/ecapa-tdnn" in metrics:
        node_ids.append("scoring/ecapa_tdnn_sim")
    if "sim/eres2net" in metrics:
        node_ids.append("scoring/eres2net_sim")
    if "dnsmos" in metrics:
        node_ids.append("scoring/dnsmos")
    if "wv-mos" in metrics:
        node_ids.append("scoring/wv_mos")
    if "utmos" in metrics:
        node_ids.append("scoring/utmos")
    for node_id in node_ids:
        node_dir = Path("src/sure_eval/evaluation/nodes") / node_id
        python_bin = node_dir / ".venv" / "bin" / "python"
        usable = python_bin.exists() or python_bin.is_symlink()
        print("node_local_python", node_id, usable, python_bin)
        if not usable:
            raise FileNotFoundError(python_bin)
    raise SystemExit(0)
if metrics & {"tts_wer", "vc_wer"}:
    transformers = importlib.import_module("transformers")
    from sure_eval.evaluation.nodes.transcription.common.providers import WhisperLargeV3Transcriber

    cache = Path("src/sure_eval/evaluation/nodes/transcription/whisper_large_v3/checkpoints")
    print("transformers", getattr(transformers, "__version__", "imported"))
    print("whisper_cache", cache.exists(), cache)
    print("transcriber_class", WhisperLargeV3Transcriber.__name__)
if metrics & {"tts_cer", "vc_cer"}:
    funasr = importlib.import_module("funasr")
    from sure_eval.evaluation.nodes.transcription.common.providers import ParaformerZHTranscriber

    cache = Path("src/sure_eval/evaluation/nodes/transcription/paraformer_zh/checkpoints")
    print("funasr", getattr(funasr, "__version__", "imported"))
    print("paraformer_cache", cache.exists(), cache)
    print("transcriber_class", ParaformerZHTranscriber.__name__)
PY

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
  "$PYTHON_BIN" - "$RUN_DIR" "$DEVICE" "${DATASET_ARRAY[@]}" -- "${METRIC_ARRAY[@]}" <<'PY'
import json
from pathlib import Path
import sys

run_dir = Path(sys.argv[1])
device = sys.argv[2]
separator = sys.argv.index("--")
datasets = sys.argv[3:separator]
metrics = [item.lower() for item in sys.argv[separator + 1 :]]

def _dataset_language(dataset: str) -> str:
    dataset_jsonl = Path("data/datasets/sure_benchmark/jsonl") / f"{dataset}.jsonl"
    language = "en"
    if not dataset_jsonl.exists():
        return language
    for line in dataset_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        return str(json.loads(line).get("language", language))
    return language

def _metric_applies_to_language(metric: str, language: str) -> bool:
    is_zh = language.lower().startswith(("zh", "cmn", "yue"))
    if metric in {"tts_cer", "vc_cer"}:
        return is_zh
    if metric in {"tts_wer", "vc_wer"}:
        return not is_zh
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
    language = _dataset_language(dataset)
    for metric in metrics:
        if _metric_applies_to_language(metric, language):
            semantic_probe_cases.append({"dataset": dataset, "language": language, "metric": metric})

if not semantic_probe_cases:
    raise SystemExit("PROBE_TRANSCRIBE=1 but no language-compatible semantic metric was selected")

from sure_eval.evaluation.audio_runtime import build_tts_runtime

for case in semantic_probe_cases:
    dataset = case["dataset"]
    language = case["language"]
    metric = case["metric"]
    prediction_file = run_dir / "predictions" / f"{dataset}.txt"
    first = prediction_file.read_text(encoding="utf-8").splitlines()[0]
    sample_id, audio_path = first.split("\t", 1)
    audio = _resolve_probe_audio_path(audio_path)
    if not audio.exists():
        raise FileNotFoundError(audio)
    runtime = build_tts_runtime(metrics=(metric,), language=language, device=device, cache_dir=None)
    key = "zh" if metric in {"tts_cer", "vc_cer"} or language.lower().startswith(("zh", "cmn", "yue")) else "en"
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
    --output "$RUN_DIR/evaluation_payload.json"
  )
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
  --record
  --output "${SEGMENT_DIR:-$RUN_DIR}/evaluation_payload.json"
)
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
