#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"

IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_kws_wenwen:v1.0}"
CONTAINER_NAME="${CONTAINER_NAME:-sure_kws_wenwen_validate_v1}"
WEKWS_GPU="${WEKWS_GPU:-0}"

MODEL_DIR="${MODEL_DIR:-${SCRIPT_DIR}}"
MODEL_DIR="$(readlink -f "${MODEL_DIR}")"

MODEL_RUNTIME="${MODEL_RUNTIME:-${MODEL_DIR}/.runtime}"
MODEL_RUNTIME="$(readlink -f "${MODEL_RUNTIME}")"

MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-${MODEL_RUNTIME}/modelscope_cache}"
MODELSCOPE_CACHE="$(readlink -f "${MODELSCOPE_CACHE}")"

ARTIFACTS_DIR="${ARTIFACTS_DIR:-${MODEL_DIR}/docker_artifacts}"
mkdir -p "${ARTIFACTS_DIR}"
ARTIFACTS_DIR="$(readlink -f "${ARTIFACTS_DIR}")"
chmod a+rwx "${ARTIFACTS_DIR}"

CONTAINER_MODEL_DIR="/workspace/sure-eval/src/sure_eval/models/daydream_factory__keyword-spot-fsmn-ctc-wenwen"
CONTAINER_SRC_DIR="/workspace/sure-eval/src/sure_eval"

for required_path in \
  "${REPO_ROOT}/src/sure_eval/__init__.py" \
  "${REPO_ROOT}/src/sure_eval/evaluation" \
  "${REPO_ROOT}/src/sure_eval/models/__init__.py" \
  "${MODEL_DIR}/model.py" \
  "${MODEL_DIR}/validate.py" \
  "${MODEL_DIR}/config.yaml" \
  "${MODEL_DIR}/model.spec.yaml" \
  "${MODEL_DIR}/server.py" \
  "${MODEL_DIR}/fixture" \
  "${MODEL_DIR}/.runtime/source/wekws" \
  "${MODELSCOPE_CACHE}"; do
  if [ ! -e "${required_path}" ]; then
    echo "Missing required path: ${required_path}" >&2
    exit 1
  fi
done

docker run --rm --gpus "device=${WEKWS_GPU}" \
  --name "${CONTAINER_NAME}" \
  -e "WEKWS_GPU=0" \
  -e "WEKWS_KEYWORDS=${WEKWS_KEYWORDS:-你好问问,嗨小问}" \
  -e "WEKWS_THRESHOLD=${WEKWS_THRESHOLD:-0.0}" \
  -v "${REPO_ROOT}/src/sure_eval/__init__.py:${CONTAINER_SRC_DIR}/__init__.py:ro" \
  -v "${REPO_ROOT}/src/sure_eval/cli.py:${CONTAINER_SRC_DIR}/cli.py:ro" \
  -v "${REPO_ROOT}/src/sure_eval/agent:${CONTAINER_SRC_DIR}/agent:ro" \
  -v "${REPO_ROOT}/src/sure_eval/core:${CONTAINER_SRC_DIR}/core:ro" \
  -v "${REPO_ROOT}/src/sure_eval/datasets:${CONTAINER_SRC_DIR}/datasets:ro" \
  -v "${REPO_ROOT}/src/sure_eval/evaluation:${CONTAINER_SRC_DIR}/evaluation:ro" \
  -v "${REPO_ROOT}/src/sure_eval/inference:${CONTAINER_SRC_DIR}/inference:ro" \
  -v "${REPO_ROOT}/src/sure_eval/protocols:${CONTAINER_SRC_DIR}/protocols:ro" \
  -v "${REPO_ROOT}/src/sure_eval/reports:${CONTAINER_SRC_DIR}/reports:ro" \
  -v "${REPO_ROOT}/src/sure_eval/tools:${CONTAINER_SRC_DIR}/tools:ro" \
  -v "${REPO_ROOT}/src/sure_eval/utils:${CONTAINER_SRC_DIR}/utils:ro" \
  -v "${REPO_ROOT}/src/sure_eval/models/__init__.py:${CONTAINER_SRC_DIR}/models/__init__.py:ro" \
  -v "${REPO_ROOT}/src/sure_eval/models/base.py:${CONTAINER_SRC_DIR}/models/base.py:ro" \
  -v "${REPO_ROOT}/src/sure_eval/models/registry.py:${CONTAINER_SRC_DIR}/models/registry.py:ro" \
  -v "${REPO_ROOT}/src/sure_eval/models/model_mapping.py:${CONTAINER_SRC_DIR}/models/model_mapping.py:ro" \
  -v "${MODEL_DIR}/__init__.py:${CONTAINER_MODEL_DIR}/__init__.py:ro" \
  -v "${MODEL_DIR}/model.py:${CONTAINER_MODEL_DIR}/model.py:ro" \
  -v "${MODEL_DIR}/validate.py:${CONTAINER_MODEL_DIR}/validate.py:ro" \
  -v "${MODEL_DIR}/config.yaml:${CONTAINER_MODEL_DIR}/config.yaml:ro" \
  -v "${MODEL_DIR}/model.spec.yaml:${CONTAINER_MODEL_DIR}/model.spec.yaml:ro" \
  -v "${MODEL_DIR}/server.py:${CONTAINER_MODEL_DIR}/server.py:ro" \
  -v "${MODEL_DIR}/fixture:${CONTAINER_MODEL_DIR}/fixture:ro" \
  -v "${MODEL_DIR}/.runtime/source/wekws:${CONTAINER_MODEL_DIR}/.runtime/source/wekws:ro" \
  -v "${MODELSCOPE_CACHE}:${CONTAINER_MODEL_DIR}/.runtime/modelscope_cache:ro" \
  -v "${ARTIFACTS_DIR}:${CONTAINER_MODEL_DIR}/artifacts" \
  "${IMAGE_TAG}" \
  bash -lc 'export PYTHONPATH=/workspace/sure-eval/src:${PYTHONPATH:-}; ln -sfn /opt/kws_wenwen_venv /workspace/sure-eval/.venv; /opt/kws_wenwen_venv/bin/python validate.py'

echo "Artifacts written to: ${ARTIFACTS_DIR}"
