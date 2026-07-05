#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"
MODEL_DIR="$(readlink -f "${SCRIPT_DIR}")"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-reonboard_whisper_large_v3_turbo:v1.0}"
CONTAINER_NAME="${CONTAINER_NAME:-sure_reonboard_whisper_large_v3_turbo_validate}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${MODEL_DIR}/docker_artifacts}"
mkdir -p "${ARTIFACTS_DIR}"
ARTIFACTS_DIR="$(readlink -f "${ARTIFACTS_DIR}")"
chmod a+rwx "${ARTIFACTS_DIR}"
CHECKPOINT_FILE="$(readlink -f "${MODEL_DIR}/checkpoints/large-v3-turbo.pt")"

CONTAINER_MODEL_DIR="/workspace/sure-eval/src/sure_eval/models_reonboard/runs/whisper_large_v3_turbo"
CONTAINER_SRC_DIR="/workspace/sure-eval/src/sure_eval"
CONTAINER_CHECKPOINT_DIR="/model_weights/whisper"

for required_path in \
  "${MODEL_DIR}/model.py" \
  "${MODEL_DIR}/validate.py" \
  "${MODEL_DIR}/model.spec.yaml" \
  "${MODEL_DIR}/fixture" \
  "${CHECKPOINT_FILE}"; do
  if [ ! -e "${required_path}" ]; then
    echo "Missing required path: ${required_path}" >&2
    exit 1
  fi
done

docker run --rm --gpus all \
  --name "${CONTAINER_NAME}" \
  -e "WHISPER_DOWNLOAD_ROOT=${CONTAINER_CHECKPOINT_DIR}" \
  -e "MODEL_ID=turbo" \
  -e "DEVICE=${DEVICE:-auto}" \
  -v "${REPO_ROOT}/src/sure_eval:${CONTAINER_SRC_DIR}:ro" \
  -v "${MODEL_DIR}:${CONTAINER_MODEL_DIR}:ro" \
  -v "${CHECKPOINT_FILE}:${CONTAINER_CHECKPOINT_DIR}/large-v3-turbo.pt:ro" \
  -v "${ARTIFACTS_DIR}:${CONTAINER_MODEL_DIR}/artifacts" \
  "${IMAGE_TAG}" \
  bash -lc 'cd /workspace/sure-eval/src/sure_eval/models_reonboard/runs/whisper_large_v3_turbo && export PYTHONPATH=/workspace/sure-eval/src:${PYTHONPATH:-}; python validate.py'

echo "Artifacts written to: ${ARTIFACTS_DIR}"
