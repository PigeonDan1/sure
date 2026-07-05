#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"

DEFAULT_IMAGE_TAG="docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_openbmb__voxcpm2:v1.0"
IMAGE_TAG="${IMAGE_TAG:-${DEFAULT_IMAGE_TAG}}"
GPU_DEVICE="${GPU_DEVICE:-0}"
DEVICE="${DEVICE:-cuda:0}"

HOST_MODEL_DIR="${MODEL_DIR:-${SCRIPT_DIR}}"
HOST_MODEL_DIR="$(readlink -f "${HOST_MODEL_DIR}")"
HOST_ARTIFACTS_DIR="${ARTIFACTS_DIR:-${HOST_MODEL_DIR}/docker_artifacts}"
HOST_ARTIFACTS_DIR="$(readlink -m "${HOST_ARTIFACTS_DIR}")"
mkdir -p "${HOST_ARTIFACTS_DIR}"
chmod a+rwx "${HOST_ARTIFACTS_DIR}"

CONTAINER_MODEL_DIR="/workspace/sure-eval/src/sure_eval/models/openbmb__VoxCPM2"
CONTAINER_SRC_DIR="/workspace/sure-eval/src/sure_eval"
LOG_PATH="${HOST_ARTIFACTS_DIR}/docker_validate.log"

for required_path in \
  "${REPO_ROOT}/src/sure_eval/__init__.py" \
  "${REPO_ROOT}/src/sure_eval/models/__init__.py" \
  "${HOST_MODEL_DIR}/__init__.py" \
  "${HOST_MODEL_DIR}/model.py" \
  "${HOST_MODEL_DIR}/server.py" \
  "${HOST_MODEL_DIR}/validate.py" \
  "${HOST_MODEL_DIR}/model.spec.yaml" \
  "${HOST_MODEL_DIR}/fixture" \
  "${HOST_MODEL_DIR}/checkpoints" \
  "${HOST_MODEL_DIR}/.runtime"; do
  if [ ! -e "${required_path}" ]; then
    echo "Missing required path: ${required_path}" >&2
    exit 1
  fi
done

{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "image_tag=${IMAGE_TAG}"
  echo "gpu_device=${GPU_DEVICE}"
  echo "device=${DEVICE}"
  echo "host_model_dir=${HOST_MODEL_DIR}"
  echo "host_artifacts_dir=${HOST_ARTIFACTS_DIR}"
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
    docker run --rm --gpus "\"device=${GPU_DEVICE}\"" \
      -e PATH="/opt/VoxCPM2_venv/bin:/opt/conda/bin:/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      -e PYTHONPATH="/workspace/sure-eval/src" \
      -e DEVICE="${DEVICE}" \
      -e HF_HOME="${CONTAINER_MODEL_DIR}/.runtime/hf-home" \
      -e HF_HUB_CACHE="${CONTAINER_MODEL_DIR}/.runtime/hf-home/hub" \
      -e HUGGINGFACE_HUB_CACHE="${CONTAINER_MODEL_DIR}/.runtime/hf-home/hub" \
      -e TRANSFORMERS_CACHE="${CONTAINER_MODEL_DIR}/.runtime/hf-home/transformers" \
      -e MPLCONFIGDIR="${CONTAINER_MODEL_DIR}/.runtime/matplotlib" \
      -e TMPDIR="${CONTAINER_MODEL_DIR}/.runtime/tmp" \
      -w "${CONTAINER_MODEL_DIR}" \
      -v "${REPO_ROOT}/src/sure_eval/__init__.py:${CONTAINER_SRC_DIR}/__init__.py:ro" \
      -v "${REPO_ROOT}/src/sure_eval/models/__init__.py:${CONTAINER_SRC_DIR}/models/__init__.py:ro" \
      -v "${HOST_MODEL_DIR}/__init__.py:${CONTAINER_MODEL_DIR}/__init__.py:ro" \
      -v "${HOST_MODEL_DIR}/model.py:${CONTAINER_MODEL_DIR}/model.py:ro" \
      -v "${HOST_MODEL_DIR}/server.py:${CONTAINER_MODEL_DIR}/server.py:ro" \
      -v "${HOST_MODEL_DIR}/validate.py:${CONTAINER_MODEL_DIR}/validate.py:ro" \
      -v "${HOST_MODEL_DIR}/model.spec.yaml:${CONTAINER_MODEL_DIR}/model.spec.yaml:ro" \
      -v "${HOST_MODEL_DIR}/fixture:${CONTAINER_MODEL_DIR}/fixture:ro" \
      -v "${HOST_MODEL_DIR}/checkpoints:${CONTAINER_MODEL_DIR}/checkpoints:ro" \
      -v "${HOST_MODEL_DIR}/.runtime:${CONTAINER_MODEL_DIR}/.runtime" \
      -v "${HOST_ARTIFACTS_DIR}:${CONTAINER_MODEL_DIR}/artifacts" \
      "${IMAGE_TAG}" \
      /bin/bash -lc '/opt/VoxCPM2_venv/bin/python - <<'"'"'PY'"'"'
import sys
import torch

print(f"python={sys.executable}")
print(f"torch={torch.__version__}")
print(f"torch.version.cuda={torch.version.cuda}")
print(f"torch.cuda.is_available={torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"torch.cuda.device_count={torch.cuda.device_count()}")
    print(f"torch.cuda.device_name={torch.cuda.get_device_name(0)}")
PY
/opt/VoxCPM2_venv/bin/python validate.py'
  echo "docker_validate_status=passed"
} 2>&1 | tee "${LOG_PATH}"

echo "Docker artifacts written to: ${HOST_ARTIFACTS_DIR}"
