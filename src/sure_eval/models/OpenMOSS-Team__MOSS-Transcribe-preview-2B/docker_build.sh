#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
cd "${REPO_ROOT}"

IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_moss_transcribe_preview_2b:v1.0}"
BASE_IMAGE="${BASE_IMAGE:-docker.v2.aispeech.com/sjtu/sjtu_yukai-grace-asr-tts-cuda-notconda:v0.18.grpo.5090.torch28.transformers4571}"

mkdir -p "${SCRIPT_DIR}/artifacts"
{
  echo "DOCKER_BUILD started at $(date -Is)"
  echo "IMAGE_TAG=${IMAGE_TAG}"
  echo "BASE_IMAGE=${BASE_IMAGE}"
  docker build \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    -f "${SCRIPT_DIR}/Dockerfile" \
    -t "${IMAGE_TAG}" \
    "${SCRIPT_DIR}"
  docker image inspect "${IMAGE_TAG}" >/dev/null
  echo "DOCKER_BUILD finished at $(date -Is)"
} 2>&1 | tee "${SCRIPT_DIR}/artifacts/build.log"
