#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"
MODEL_DIR="$(readlink -f "${SCRIPT_DIR}")"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-reonboard_asr_qwen3:v1.0}"
CONTAINER_NAME="${CONTAINER_NAME:-sure_reonboard_asr_qwen3_validate}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${MODEL_DIR}/docker_artifacts}"
mkdir -p "${ARTIFACTS_DIR}"
ARTIFACTS_DIR="$(readlink -f "${ARTIFACTS_DIR}")"
chmod a+rwx "${ARTIFACTS_DIR}"
WEIGHTS_DIR="$(readlink -f "${MODEL_DIR}/.runtime/modelscope_cache/models/Qwen/Qwen3-ASR-1___7B")"

CONTAINER_MODEL_DIR="/workspace/sure-eval/src/sure_eval/models_reonboard/runs/asr_qwen3"
CONTAINER_SRC_DIR="/workspace/sure-eval/src/sure_eval"
CONTAINER_WEIGHTS_DIR="/model_weights/Qwen3-ASR-1___7B"

for required_path in \
  "${MODEL_DIR}/model.py" \
  "${MODEL_DIR}/validate.py" \
  "${MODEL_DIR}/model.spec.yaml" \
  "${MODEL_DIR}/fixture" \
  "${WEIGHTS_DIR}"; do
  if [ ! -e "${required_path}" ]; then
    echo "Missing required path: ${required_path}" >&2
    exit 1
  fi
done

docker run --rm --gpus all \
  --name "${CONTAINER_NAME}" \
  -e "MODEL_PATH=${CONTAINER_WEIGHTS_DIR}" \
  -e "MODELSCOPE_CACHE=/model_weights/modelscope_cache" \
  -e "DEVICE=cuda" \
  -e "REQUIRE_GPU=1" \
  -v "${REPO_ROOT}/src/sure_eval:${CONTAINER_SRC_DIR}:ro" \
  -v "${MODEL_DIR}:${CONTAINER_MODEL_DIR}:ro" \
  -v "${WEIGHTS_DIR}:${CONTAINER_WEIGHTS_DIR}:ro" \
  -v "${ARTIFACTS_DIR}:${CONTAINER_MODEL_DIR}/artifacts" \
  "${IMAGE_TAG}" \
  bash -lc 'cd /workspace/sure-eval/src/sure_eval/models_reonboard/runs/asr_qwen3 && export PYTHONPATH=/workspace/sure-eval/src:${PYTHONPATH:-}; /opt/asr_qwen3_venv/bin/python validate.py'

echo "Artifacts written to: ${ARTIFACTS_DIR}"
