#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"

IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_funaudiollm__fun-cosyvoice3-0.5b-2512:v1.0}"
GPU_DEVICE="${GPU_DEVICE:-0}"
CONTAINER_NAME="${CONTAINER_NAME:-sure_fun_cosyvoice3_validate}"

MODEL_DIR="${MODEL_DIR:-${SCRIPT_DIR}}"
MODEL_DIR="$(readlink -f "${MODEL_DIR}")"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${MODEL_DIR}/docker_artifacts}"
mkdir -p "${ARTIFACTS_DIR}" "${MODEL_DIR}/artifacts" "${MODEL_DIR}/.runtime/triton-cache" "${MODEL_DIR}/.runtime/modelscope_cache" "${MODEL_DIR}/.runtime/huggingface"
ARTIFACTS_DIR="$(readlink -f "${ARTIFACTS_DIR}")"

CONTAINER_MODEL_DIR="/workspace/sure-eval/src/sure_eval/models/FunAudioLLM__Fun-CosyVoice3-0.5B-2512"
CONTAINER_SRC_DIR="/workspace/sure-eval/src/sure_eval"
CONTAINER_SOURCE_DIR="${CONTAINER_MODEL_DIR}/.runtime/source/CosyVoice"

for required_path in \
  "${REPO_ROOT}/src/sure_eval/__init__.py" \
  "${MODEL_DIR}/model.py" \
  "${MODEL_DIR}/validate.py" \
  "${MODEL_DIR}/server.py" \
  "${MODEL_DIR}/model.spec.yaml" \
  "${MODEL_DIR}/fixture/tts/en/gt.jsonl" \
  "${MODEL_DIR}/fixture/tts/en/zero_shot_prompt.wav" \
  "${MODEL_DIR}/.runtime/source/CosyVoice/cosyvoice/cli/cosyvoice.py" \
  "${MODEL_DIR}/.runtime/source/CosyVoice/third_party/Matcha-TTS/matcha/__init__.py" \
  "${MODEL_DIR}/.runtime/source/CosyVoice/asset/zero_shot_prompt.wav" \
  "${MODEL_DIR}/.runtime/source/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B/cosyvoice3.yaml" \
  "${MODEL_DIR}/.runtime/source/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B/llm.pt" \
  "${MODEL_DIR}/.runtime/source/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B/flow.pt" \
  "${MODEL_DIR}/.runtime/source/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B/hift.pt" \
  "${MODEL_DIR}/.runtime/source/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B/speech_tokenizer_v3.onnx" \
  "${MODEL_DIR}/.runtime/source/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B/CosyVoice-BlankEN/model.safetensors"; do
  if [ ! -e "${required_path}" ]; then
    echo "Missing required path: ${required_path}" >&2
    exit 1
  fi
done

run_log="${ARTIFACTS_DIR}/docker_validate.log"
rm -f "${run_log}"
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

docker run --rm --gpus "\"device=${GPU_DEVICE}\"" --ipc=host \
  --name "${CONTAINER_NAME}" \
  -e "DEVICE=${DEVICE:-cuda:0}" \
  -e "MODEL_PATH=${CONTAINER_SOURCE_DIR}/pretrained_models/Fun-CosyVoice3-0.5B" \
  -e "PYTHONPATH=/workspace/sure-eval/src:${CONTAINER_MODEL_DIR}:${CONTAINER_SOURCE_DIR}:${CONTAINER_SOURCE_DIR}/third_party/Matcha-TTS" \
  -e "TRITON_CACHE_DIR=${CONTAINER_MODEL_DIR}/.runtime/triton-cache" \
  -e "MODELSCOPE_CACHE=${CONTAINER_MODEL_DIR}/.runtime/modelscope_cache" \
  -e "MODELSCOPE_MODULES_CACHE=${CONTAINER_MODEL_DIR}/.runtime/modelscope_modules" \
  -e "MODELSCOPE_INDEX_FILE=${CONTAINER_MODEL_DIR}/.runtime/modelscope_ast_indexer" \
  -e "HF_HOME=${CONTAINER_MODEL_DIR}/.runtime/huggingface" \
  -e "HF_HUB_CACHE=${CONTAINER_MODEL_DIR}/.runtime/huggingface/hub" \
  -w "${CONTAINER_MODEL_DIR}" \
  -v "${REPO_ROOT}/src/sure_eval/__init__.py:${CONTAINER_SRC_DIR}/__init__.py:ro" \
  -v "${REPO_ROOT}/src/sure_eval/evaluation:${CONTAINER_SRC_DIR}/evaluation:ro" \
  -v "${MODEL_DIR}:${CONTAINER_MODEL_DIR}" \
  "${IMAGE_TAG}" \
  bash -lc '/opt/conda/bin/python validate.py' 2>&1 | tee "${run_log}"

if grep -q "Error: exit status" "${run_log}"; then
  echo "Docker wrapper reported an inner command failure." >&2
  exit 6
fi

echo "Artifacts written to: ${ARTIFACTS_DIR}"
