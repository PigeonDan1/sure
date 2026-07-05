#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${MODEL_DIR}/../../../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_nvidia__parakeet-rnnt-1.1b:v1.0}"
CONTAINER_MODEL_DIR="/workspace/sure-eval/src/sure_eval/models/nvidia__parakeet-rnnt-1.1b"

mkdir -p "${MODEL_DIR}/docker_artifacts"

docker run --rm \
  -e HF_HOME="${CONTAINER_MODEL_DIR}/.runtime/hf-home" \
  -e HUGGINGFACE_HUB_CACHE="${CONTAINER_MODEL_DIR}/.runtime/hf-home/hub" \
  -e NEMO_CACHE_DIR="${CONTAINER_MODEL_DIR}/.runtime/nemo-cache" \
  -e DEVICE=cpu \
  -e PYTHONPATH=/workspace/sure-eval/src \
  -v "${REPO_ROOT}:/workspace/sure-eval:ro" \
  -v "${MODEL_DIR}/.runtime:${CONTAINER_MODEL_DIR}/.runtime" \
  -v "${MODEL_DIR}/checkpoints:${CONTAINER_MODEL_DIR}/checkpoints:ro" \
  -v "${MODEL_DIR}/artifacts:${CONTAINER_MODEL_DIR}/artifacts" \
  -v "${MODEL_DIR}/docker_artifacts:${CONTAINER_MODEL_DIR}/docker_artifacts" \
  "${IMAGE_TAG}" \
  /opt/parakeet_rnnt_venv/bin/python "${CONTAINER_MODEL_DIR}/validate.py" \
  2>&1 | tee "${MODEL_DIR}/docker_artifacts/docker_validate.log"
