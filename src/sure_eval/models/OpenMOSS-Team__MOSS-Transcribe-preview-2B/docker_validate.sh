#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"
MODEL_DIR="$(readlink -f "${SCRIPT_DIR}")"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${MODEL_DIR}/artifacts}"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_moss_transcribe_preview_2b:v1.0}"
STAGE="${1:-all}"

mkdir -p "${MODEL_DIR}/.runtime/hf-home/hub" "${MODEL_DIR}/.runtime/tmp" "${MODEL_DIR}/.runtime/matplotlib" "${ARTIFACTS_DIR}"
chmod a+rwx "${ARTIFACTS_DIR}" "${MODEL_DIR}/.runtime" "${MODEL_DIR}/.runtime/hf-home" "${MODEL_DIR}/.runtime/hf-home/hub" "${MODEL_DIR}/.runtime/tmp" "${MODEL_DIR}/.runtime/matplotlib"

CONTAINER_MODEL_DIR="/workspace/sure-eval/src/sure_eval/models/OpenMOSS-Team__MOSS-Transcribe-preview-2B"

for required_path in \
  "${MODEL_DIR}/model.py" \
  "${MODEL_DIR}/validate.py" \
  "${MODEL_DIR}/validate_runtime.py" \
  "${MODEL_DIR}/fetch_weights.py" \
  "${MODEL_DIR}/fixture" \
  "${MODEL_DIR}/model.spec.yaml"; do
  if [ ! -e "${required_path}" ]; then
    echo "Missing required path: ${required_path}" >&2
    exit 1
  fi
done

docker run --rm --gpus '"device=0"' \
  -e "DEVICE=${DEVICE:-cuda:0}" \
  -e "HF_HOME=${CONTAINER_MODEL_DIR}/.runtime/hf-home" \
  -e "HF_HUB_CACHE=${CONTAINER_MODEL_DIR}/.runtime/hf-home/hub" \
  -e "TRANSFORMERS_CACHE=${CONTAINER_MODEL_DIR}/.runtime/hf-home/hub" \
  -e "MPLCONFIGDIR=${CONTAINER_MODEL_DIR}/.runtime/matplotlib" \
  -e "TMPDIR=${CONTAINER_MODEL_DIR}/.runtime/tmp" \
  -e "HF_HUB_ENABLE_HF_TRANSFER=${HF_HUB_ENABLE_HF_TRANSFER:-0}" \
  -e "HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-0}" \
  -e "MAX_NEW_TOKENS=${MAX_NEW_TOKENS:-512}" \
  -v "${MODEL_DIR}:${CONTAINER_MODEL_DIR}" \
  -w "${CONTAINER_MODEL_DIR}" \
  "${IMAGE_TAG}" \
  bash -lc "python -m py_compile model.py server.py validate.py validate_runtime.py fetch_weights.py && python fetch_weights.py && python validate_runtime.py --stage ${STAGE} && python validate.py"
