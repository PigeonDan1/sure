#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"

IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-reonboard_indextts2:v1.0}"
GPU_DEVICE="${GPU_DEVICE:-0}"
CONTAINER_NAME="${CONTAINER_NAME:-sure_reonboard_indextts2_validate}"

MODEL_DIR="${MODEL_DIR:-${SCRIPT_DIR}}"
MODEL_DIR="$(readlink -f "${MODEL_DIR}")"
INDEX_MODEL_ROOT="${INDEX_MODEL_ROOT:-${MODEL_DIR}/.runtime/modelscope_cache/IndexTeam/IndexTTS-2}"
INDEX_MODEL_ROOT="$(readlink -f "${INDEX_MODEL_ROOT}")"
INDEX_SOURCE_ROOT="${INDEX_SOURCE_ROOT:-${MODEL_DIR}/.runtime/source/index-tts}"
INDEX_SOURCE_ROOT="$(readlink -f "${INDEX_SOURCE_ROOT}")"
INDEX_HF_HUB="${INDEX_HF_HUB:-${MODEL_DIR}/.runtime/huggingface/hub}"
INDEX_HF_HUB="$(readlink -f "${INDEX_HF_HUB}")"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${MODEL_DIR}/docker_artifacts}"
mkdir -p "${ARTIFACTS_DIR}/outputs"
ARTIFACTS_DIR="$(readlink -f "${ARTIFACTS_DIR}")"
chmod a+rwx "${ARTIFACTS_DIR}"

CONTAINER_MODEL_DIR="/workspace/sure-eval/src/sure_eval/models_reonboard/runs/IndexTeam__IndexTTS-2"
CONTAINER_SRC_DIR="/workspace/sure-eval/src/sure_eval"
CONTAINER_INDEX_MODEL_ROOT="/model_weights/IndexTeam/IndexTTS-2"
CONTAINER_INDEX_SOURCE_ROOT="/runtime/source/index-tts"
CONTAINER_HF_HUB="${CONTAINER_MODEL_DIR}/.runtime/huggingface/hub"

for required_path in \
  "${REPO_ROOT}/src/sure_eval/__init__.py" \
  "${MODEL_DIR}/model.py" \
  "${MODEL_DIR}/validate.py" \
  "${MODEL_DIR}/config.yaml" \
  "${MODEL_DIR}/model.spec.yaml" \
  "${MODEL_DIR}/server.py" \
  "${MODEL_DIR}/fixture" \
  "${MODEL_DIR}/artifacts/backend_choice.json" \
  "${MODEL_DIR}/artifacts/build_plan.json" \
  "${MODEL_DIR}/artifacts/spec_validation.json" \
  "${MODEL_DIR}/artifacts/weights_manifest.json" \
  "${INDEX_MODEL_ROOT}/config.yaml" \
  "${INDEX_MODEL_ROOT}/gpt.pth" \
  "${INDEX_MODEL_ROOT}/s2mel.pth" \
  "${INDEX_SOURCE_ROOT}/indextts/infer_v2.py"; do
  if [ ! -e "${required_path}" ]; then
    echo "Missing required path: ${required_path}" >&2
    exit 1
  fi
done

run_log="${ARTIFACTS_DIR}/docker_validate.stdout.log"
rm -f "${run_log}"
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run --rm --gpus "\"device=${GPU_DEVICE}\"" \
  --name "${CONTAINER_NAME}" \
  -e "DEVICE=${DEVICE:-cuda:0}" \
  -e "MPLCONFIGDIR=${CONTAINER_MODEL_DIR}/docker_artifacts/matplotlib" \
  -e "INDEXTTS2_MODEL_ROOT=${CONTAINER_INDEX_MODEL_ROOT}" \
  -e "INDEXTTS2_SOURCE_ROOT=${CONTAINER_INDEX_SOURCE_ROOT}" \
  -e "HF_HOME=${CONTAINER_MODEL_DIR}/.runtime/huggingface" \
  -e "HF_HUB_CACHE=${CONTAINER_HF_HUB}" \
  -e "ARTIFACTS_DIR=${CONTAINER_MODEL_DIR}/docker_artifacts" \
  -e "PYTHONPATH=/workspace/sure-eval/src:${CONTAINER_INDEX_SOURCE_ROOT}" \
  -w "${CONTAINER_MODEL_DIR}" \
  -v "${REPO_ROOT}/src/sure_eval/__init__.py:${CONTAINER_SRC_DIR}/__init__.py:ro" \
  -v "${REPO_ROOT}/src/sure_eval/evaluation:${CONTAINER_SRC_DIR}/evaluation:ro" \
  -v "${MODEL_DIR}/__init__.py:${CONTAINER_MODEL_DIR}/__init__.py:ro" \
  -v "${MODEL_DIR}/model.py:${CONTAINER_MODEL_DIR}/model.py:ro" \
  -v "${MODEL_DIR}/validate.py:${CONTAINER_MODEL_DIR}/validate.py:ro" \
  -v "${MODEL_DIR}/config.yaml:${CONTAINER_MODEL_DIR}/config.yaml:ro" \
  -v "${MODEL_DIR}/model.spec.yaml:${CONTAINER_MODEL_DIR}/model.spec.yaml:ro" \
  -v "${MODEL_DIR}/server.py:${CONTAINER_MODEL_DIR}/server.py:ro" \
  -v "${MODEL_DIR}/fixture:${CONTAINER_MODEL_DIR}/fixture:ro" \
  -v "${MODEL_DIR}/artifacts:${CONTAINER_MODEL_DIR}/artifacts:ro" \
  -v "${INDEX_MODEL_ROOT}:${CONTAINER_INDEX_MODEL_ROOT}:ro" \
  -v "${INDEX_SOURCE_ROOT}:${CONTAINER_INDEX_SOURCE_ROOT}:ro" \
  -v "${INDEX_HF_HUB}:${CONTAINER_HF_HUB}:ro" \
  -v "${ARTIFACTS_DIR}:${CONTAINER_MODEL_DIR}/docker_artifacts" \
  "${IMAGE_TAG}" \
  bash -lc 'python validate.py' 2>&1 | tee "${run_log}"

if grep -q "Error: exit status" "${run_log}"; then
  echo "Docker wrapper reported an inner command failure." >&2
  exit 6
fi

echo "Artifacts written to: ${ARTIFACTS_DIR}"
