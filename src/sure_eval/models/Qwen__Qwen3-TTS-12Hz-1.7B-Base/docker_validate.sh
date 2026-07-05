#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"
MODEL_DIR="${MODEL_DIR:-${SCRIPT_DIR}}"
MODEL_DIR="$(readlink -f "${MODEL_DIR}")"

IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_qwen__qwen3-tts-12hz-1.7b-base:v1.0}"
GPU_DEVICE="${GPU_DEVICE:-0}"
DEVICE_MAP="${DEVICE_MAP:-cuda:0}"
TORCH_DTYPE="${TORCH_DTYPE:-bfloat16}"
ATTN_IMPLEMENTATION="${ATTN_IMPLEMENTATION:-eager}"

CONTAINER_ROOT="/workspace/sure-eval"
CONTAINER_MODEL_DIR="${CONTAINER_ROOT}/src/sure_eval/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base"
CONTAINER_PYTHON="/opt/qwen3_tts_12hz_1_7b_base_venv/bin/python"

HOST_ARTIFACTS_DIR="${ARTIFACTS_DIR:-${MODEL_DIR}/docker_artifacts}"
mkdir -p "${HOST_ARTIFACTS_DIR}"
HOST_ARTIFACTS_DIR="$(readlink -f "${HOST_ARTIFACTS_DIR}")"
chmod a+rwx "${HOST_ARTIFACTS_DIR}"
RUN_LOG="${HOST_ARTIFACTS_DIR}/docker_validate.log"

for required_path in \
  "${MODEL_DIR}/model.py" \
  "${MODEL_DIR}/validate.py" \
  "${MODEL_DIR}/config.yaml" \
  "${MODEL_DIR}/model.spec.yaml" \
  "${MODEL_DIR}/server.py" \
  "${MODEL_DIR}/__init__.py" \
  "${MODEL_DIR}/fixture" \
  "${MODEL_DIR}/checkpoints" \
  "${MODEL_DIR}/.runtime"; do
  if [ ! -e "${required_path}" ]; then
    echo "Missing required path: ${required_path}" >&2
    exit 1
  fi
done

: > "${RUN_LOG}"

docker run --rm --gpus "\"device=${GPU_DEVICE}\"" \
  -e PYTHONPATH="${CONTAINER_ROOT}/src" \
  -e MODEL_PATH="${CONTAINER_MODEL_DIR}/checkpoints/Qwen3-TTS-12Hz-1.7B-Base" \
  -e HF_HOME="${CONTAINER_MODEL_DIR}/.runtime/huggingface" \
  -e HF_HUB_CACHE="${CONTAINER_MODEL_DIR}/.runtime/huggingface/hub" \
  -e MODELSCOPE_CACHE="${CONTAINER_MODEL_DIR}/.runtime/modelscope_cache" \
  -e MPLCONFIGDIR="${CONTAINER_MODEL_DIR}/.runtime/matplotlib" \
  -e TMPDIR="${CONTAINER_MODEL_DIR}/.runtime/tmp" \
  -e DEVICE_MAP="${DEVICE_MAP}" \
  -e TORCH_DTYPE="${TORCH_DTYPE}" \
  -e ATTN_IMPLEMENTATION="${ATTN_IMPLEMENTATION}" \
  -w "${CONTAINER_MODEL_DIR}" \
  -v "${MODEL_DIR}/model.py:${CONTAINER_MODEL_DIR}/model.py:ro" \
  -v "${MODEL_DIR}/validate.py:${CONTAINER_MODEL_DIR}/validate.py:ro" \
  -v "${MODEL_DIR}/config.yaml:${CONTAINER_MODEL_DIR}/config.yaml:ro" \
  -v "${MODEL_DIR}/model.spec.yaml:${CONTAINER_MODEL_DIR}/model.spec.yaml:ro" \
  -v "${MODEL_DIR}/server.py:${CONTAINER_MODEL_DIR}/server.py:ro" \
  -v "${MODEL_DIR}/__init__.py:${CONTAINER_MODEL_DIR}/__init__.py:ro" \
  -v "${MODEL_DIR}/fixture:${CONTAINER_MODEL_DIR}/fixture:ro" \
  -v "${MODEL_DIR}/checkpoints:${CONTAINER_MODEL_DIR}/checkpoints:ro" \
  -v "${MODEL_DIR}/.runtime:${CONTAINER_MODEL_DIR}/.runtime" \
  -v "${HOST_ARTIFACTS_DIR}:${CONTAINER_MODEL_DIR}/artifacts" \
  "${IMAGE_TAG}" \
  bash -lc "test ! -e ${CONTAINER_MODEL_DIR}/.venv; ${CONTAINER_PYTHON} - <<'PY'
import sys
print('container_python', sys.executable)
assert sys.executable.startswith('/opt/qwen3_tts_12hz_1_7b_base_venv/')
PY
${CONTAINER_PYTHON} validate.py" 2>&1 | tee "${RUN_LOG}"

test -s "${HOST_ARTIFACTS_DIR}/sample_output.json"
test -s "${HOST_ARTIFACTS_DIR}/outputs/qwen3_tts_1_7b_base_en_clone.wav"
echo "docker_validate_status=passed" | tee -a "${RUN_LOG}"
