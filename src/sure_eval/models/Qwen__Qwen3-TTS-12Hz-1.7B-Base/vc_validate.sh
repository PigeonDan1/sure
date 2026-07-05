#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(readlink -f "${SCRIPT_DIR}")"

normalize_for_vc() {
  local path="$1"
  if [[ "${path}" == /mnt/cloudstorfs/sjtu_home/* ]]; then
    local alt="/hpc_stor03/sjtu_home/${path#/mnt/cloudstorfs/sjtu_home/}"
    if [ -e "${alt}" ]; then
      printf '%s\n' "${alt}"
      return 0
    fi
  fi
  printf '%s\n' "${path}"
}

MODEL_DIR="$(normalize_for_vc "${SCRIPT_DIR}")"

PARTITION="${PARTITION:-pdgpu-a10}"
PROJECT="${PROJECT:-sjtu}"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_qwen__qwen3-tts-12hz-1.7b-base:v1.0}"
JOB_NAME="${JOB_NAME:-sure_qwen3_tts_1_7b_base_phase1}"
CPU_PER_TASK="${CPU_PER_TASK:-8}"
MEM_PER_TASK="${MEM_PER_TASK:-32}"
GPU_PER_TASK="${GPU_PER_TASK:-1}"

ARTIFACTS_DIR="${MODEL_DIR}/artifacts"
LOG_PATH="${LOG_PATH:-${ARTIFACTS_DIR}/vc_validate.log}"
SUBMIT_RECORD="${SUBMIT_RECORD:-${ARTIFACTS_DIR}/vc_submit.json}"
mkdir -p "${ARTIFACTS_DIR}" "${MODEL_DIR}/.runtime/tmp"

JOB_NAME="$(echo "${JOB_NAME}" | sed 's/[^a-zA-Z0-9_.-]/_/g' | cut -c1-50)"
export ARTIFACTS_DIR CPU_PER_TASK GPU_PER_TASK IMAGE_TAG JOB_NAME LOG_PATH MEM_PER_TASK MODEL_DIR PARTITION PROJECT SUBMIT_RECORD

VC_PYTHON="/opt/qwen3_tts_12hz_1_7b_base_venv/bin/python"
VC_CMD="set -o pipefail && cd ${MODEL_DIR} && mkdir -p artifacts/outputs .runtime/tmp && export PATH=/opt/qwen3_tts_12hz_1_7b_base_venv/bin:/opt/conda/bin:/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin MODEL_PATH=${MODEL_DIR}/checkpoints/Qwen3-TTS-12Hz-1.7B-Base HF_HOME=${MODEL_DIR}/.runtime/huggingface HF_HUB_CACHE=${MODEL_DIR}/.runtime/huggingface/hub MODELSCOPE_CACHE=${MODEL_DIR}/.runtime/modelscope_cache MPLCONFIGDIR=${MODEL_DIR}/.runtime/matplotlib TMPDIR=${MODEL_DIR}/.runtime/tmp DEVICE_MAP=cuda:0 TORCH_DTYPE=bfloat16 ATTN_IMPLEMENTATION=eager PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True && ${VC_PYTHON} validate.py 2>&1 | tee ${LOG_PATH}"

submit_output="$(vc submit \
  -p "${PARTITION}" \
  -i "${IMAGE_TAG}" \
  -j "${JOB_NAME}" \
  -n 1 -c "${CPU_PER_TASK}" -m "${MEM_PER_TASK}G" -g "${GPU_PER_TASK}" \
  -pj "${PROJECT}" \
  -d "${MODEL_DIR}" \
  --cmd "${VC_CMD}")"

printf '%s\n' "${submit_output}"

SUBMIT_OUTPUT="${submit_output}" python3 - "${SUBMIT_RECORD}" <<'PY'
import json
import os
import re
import sys
from datetime import datetime, timezone

submit_record = sys.argv[1]
submit_output = os.environ.get("SUBMIT_OUTPUT", "")
match = re.search(r"job-[A-Za-z0-9_.-]+", submit_output)
payload = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "job_id": match.group(0) if match else None,
    "job_name": os.environ.get("JOB_NAME"),
    "partition": os.environ.get("PARTITION"),
    "project": os.environ.get("PROJECT"),
    "image_tag": os.environ.get("IMAGE_TAG"),
    "model_dir": os.environ.get("MODEL_DIR"),
    "log_path": os.environ.get("LOG_PATH"),
    "submit_output": submit_output,
}
with open(submit_record, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=True, indent=2)
    handle.write("\n")
PY

echo "VC submit record: ${SUBMIT_RECORD}"
echo "VC log path: ${LOG_PATH}"
