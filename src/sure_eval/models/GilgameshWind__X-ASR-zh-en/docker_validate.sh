#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"

IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_x_asr_zh_en:v1.0}"
CONTAINER_NAME="${CONTAINER_NAME:-sure_x_asr_zh_en_validate_v1}"
GPU_DEVICE="${GPU_DEVICE:-all}"

MODEL_DIR="${MODEL_DIR:-${SCRIPT_DIR}}"
MODEL_DIR="$(readlink -f "${MODEL_DIR}")"

HF_MODEL_ROOT="${HF_MODEL_ROOT:-${MODEL_DIR}/.runtime/huggingface/GilgameshWind/X-ASR-zh-en}"
HF_MODEL_ROOT="$(readlink -f "${HF_MODEL_ROOT}")"

ARTIFACTS_DIR="${ARTIFACTS_DIR:-${MODEL_DIR}/docker_artifacts}"
mkdir -p "${ARTIFACTS_DIR}"
ARTIFACTS_DIR="$(readlink -f "${ARTIFACTS_DIR}")"
chmod a+rwx "${ARTIFACTS_DIR}"

CONTAINER_MODEL_DIR="/workspace/sure-eval/src/sure_eval/models/GilgameshWind__X-ASR-zh-en"
CONTAINER_SRC_DIR="/workspace/sure-eval/src/sure_eval"
CONTAINER_HF_ROOT="${CONTAINER_MODEL_DIR}/.runtime/huggingface/GilgameshWind/X-ASR-zh-en"

for required_path in \
  "${REPO_ROOT}/src/sure_eval/__init__.py" \
  "${REPO_ROOT}/src/sure_eval/models/__init__.py" \
  "${MODEL_DIR}/model.py" \
  "${MODEL_DIR}/validate.py" \
  "${MODEL_DIR}/config.yaml" \
  "${MODEL_DIR}/model.spec.yaml" \
  "${MODEL_DIR}/server.py" \
  "${MODEL_DIR}/fixture" \
  "${MODEL_DIR}/artifacts/backend_choice.json" \
  "${MODEL_DIR}/artifacts/build_plan.json" \
  "${MODEL_DIR}/artifacts/weights_manifest.json" \
  "${HF_MODEL_ROOT}/deployment/models/chunk-960ms-model/encoder-960ms.onnx" \
  "${HF_MODEL_ROOT}/deployment/models/chunk-960ms-model/decoder-960ms.onnx" \
  "${HF_MODEL_ROOT}/deployment/models/chunk-960ms-model/joiner-960ms.onnx" \
  "${HF_MODEL_ROOT}/deployment/models/chunk-960ms-model/tokens.txt"; do
  if [ ! -e "${required_path}" ]; then
    echo "Missing required path: ${required_path}" >&2
    exit 1
  fi
done

run_log="${ARTIFACTS_DIR}/docker_validate.stdout.log"
rm -f "${run_log}"

env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
docker run --rm --gpus "${GPU_DEVICE}" \
  --name "${CONTAINER_NAME}" \
  -e "X_ASR_MODEL_ROOT=${CONTAINER_HF_ROOT}" \
  -e "X_ASR_CHUNK=${X_ASR_CHUNK:-960}" \
  -e "X_ASR_TAIL_PADDING_SECONDS=${X_ASR_TAIL_PADDING_SECONDS:-1.0}" \
  -e "SHERPA_ONNX_PROVIDER=${SHERPA_ONNX_PROVIDER:-cuda}" \
  -e "SHERPA_ONNX_NUM_THREADS=${SHERPA_ONNX_NUM_THREADS:-1}" \
  -e "LD_LIBRARY_PATH=/opt/conda/lib/python3.11/site-packages/nvidia/cudnn/lib:/usr/local/cuda/lib64:/usr/local/cuda/targets/x86_64-linux/lib:/usr/local/nvidia/lib:/usr/local/nvidia/lib64" \
  -v "${REPO_ROOT}/src/sure_eval/__init__.py:${CONTAINER_SRC_DIR}/__init__.py:ro" \
  -v "${REPO_ROOT}/src/sure_eval/models/__init__.py:${CONTAINER_SRC_DIR}/models/__init__.py:ro" \
  -v "${MODEL_DIR}/__init__.py:${CONTAINER_MODEL_DIR}/__init__.py:ro" \
  -v "${MODEL_DIR}/model.py:${CONTAINER_MODEL_DIR}/model.py:ro" \
  -v "${MODEL_DIR}/validate.py:${CONTAINER_MODEL_DIR}/validate.py:ro" \
  -v "${MODEL_DIR}/config.yaml:${CONTAINER_MODEL_DIR}/config.yaml:ro" \
  -v "${MODEL_DIR}/model.spec.yaml:${CONTAINER_MODEL_DIR}/model.spec.yaml:ro" \
  -v "${MODEL_DIR}/server.py:${CONTAINER_MODEL_DIR}/server.py:ro" \
  -v "${MODEL_DIR}/fixture:${CONTAINER_MODEL_DIR}/fixture:ro" \
  -v "${HF_MODEL_ROOT}:${CONTAINER_HF_ROOT}:ro" \
  -v "${ARTIFACTS_DIR}:${CONTAINER_MODEL_DIR}/artifacts" \
  -v "${MODEL_DIR}/artifacts/backend_choice.json:${CONTAINER_MODEL_DIR}/artifacts/backend_choice.json:ro" \
  -v "${MODEL_DIR}/artifacts/build_plan.json:${CONTAINER_MODEL_DIR}/artifacts/build_plan.json:ro" \
  -v "${MODEL_DIR}/artifacts/weights_manifest.json:${CONTAINER_MODEL_DIR}/artifacts/weights_manifest.json:ro" \
  "${IMAGE_TAG}" \
  bash -lc 'export PYTHONPATH=/workspace/sure-eval/src:${PYTHONPATH:-}; ln -sfn /opt/x_asr_zh_en_venv /workspace/sure-eval/.venv; /opt/x_asr_zh_en_venv/bin/python - <<PY
import sherpa_onnx
print("SHERPA_ONNX_VERSION", getattr(sherpa_onnx, "__version__", "unknown"))
PY
/opt/x_asr_zh_en_venv/bin/python validate.py' 2>&1 | tee "${run_log}"

if grep -q "Fallback to cpu" "${run_log}"; then
  echo "Requested CUDA provider but sherpa-onnx fell back to CPU." >&2
  exit 5
fi

echo "Artifacts written to: ${ARTIFACTS_DIR}"
