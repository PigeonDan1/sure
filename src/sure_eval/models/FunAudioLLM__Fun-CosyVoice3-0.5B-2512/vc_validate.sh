#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(readlink -f "${SCRIPT_DIR}")"

PARTITION="${PARTITION:-pdgpu-a10}"
PROJECT="${PROJECT:-sjtu}"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_funaudiollm__fun-cosyvoice3-0.5b-2512:v1.0}"
JOB_NAME="${JOB_NAME:-sure_fun_cosyvoice3_phase1}"
CPU_PER_TASK="${CPU_PER_TASK:-8}"
MEM_PER_TASK="${MEM_PER_TASK:-32}"
GPU_PER_TASK="${GPU_PER_TASK:-1}"

LOG_DIR="${SCRIPT_DIR}/vc_artifacts"
mkdir -p "${LOG_DIR}" "${SCRIPT_DIR}/artifacts"
JOB_NAME="$(echo "${JOB_NAME}" | sed 's/[^a-zA-Z0-9_.-]/_/g' | cut -c1-50)"
LOG_PATH="${LOG_PATH:-${LOG_DIR}/${JOB_NAME}.log}"

submit_output="$(vc submit \
  -p "${PARTITION}" \
  -i "${IMAGE_TAG}" \
  -j "${JOB_NAME}" \
  -n 1 -c "${CPU_PER_TASK}" -m "${MEM_PER_TASK}G" -g "${GPU_PER_TASK}" \
  -pj "${PROJECT}" \
  -d "${SCRIPT_DIR}" \
  --cmd "set -o pipefail && cd ${SCRIPT_DIR} && export PYTHONPATH=/hpc_stor03/sjtu_home/junhao.du/sure-eval-sandbox/src:${SCRIPT_DIR}:${SCRIPT_DIR}/.runtime/source/CosyVoice:${SCRIPT_DIR}/.runtime/source/CosyVoice/third_party/Matcha-TTS:\${PYTHONPATH:-} MODEL_PATH=${SCRIPT_DIR}/.runtime/source/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B DEVICE=cuda:0 TRITON_CACHE_DIR=${SCRIPT_DIR}/.runtime/triton-cache MODELSCOPE_CACHE=${SCRIPT_DIR}/.runtime/modelscope_cache MODELSCOPE_MODULES_CACHE=${SCRIPT_DIR}/.runtime/modelscope_modules MODELSCOPE_INDEX_FILE=${SCRIPT_DIR}/.runtime/modelscope_ast_indexer HF_HOME=${SCRIPT_DIR}/.runtime/huggingface HF_HUB_CACHE=${SCRIPT_DIR}/.runtime/huggingface/hub PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True && /opt/conda/bin/python validate.py 2>&1 | tee ${LOG_PATH}")"
echo "${submit_output}"

{
  printf '{\n'
  printf '  "job_name": "%s",\n' "${JOB_NAME}"
  printf '  "partition": "%s",\n' "${PARTITION}"
  printf '  "project": "%s",\n' "${PROJECT}"
  printf '  "image_tag": "%s",\n' "${IMAGE_TAG}"
  printf '  "log_path": "%s",\n' "${LOG_PATH}"
  printf '  "submit_output": %s\n' "$(python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' <<<"${submit_output}")"
  printf '}\n'
} > "${SCRIPT_DIR}/artifacts/vc_submit.json"

echo "VC log path: ${LOG_PATH}"
