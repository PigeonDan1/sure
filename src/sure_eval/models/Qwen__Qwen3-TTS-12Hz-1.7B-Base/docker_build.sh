#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"
ARTIFACTS_DIR="${SCRIPT_DIR}/artifacts"
mkdir -p "${ARTIFACTS_DIR}"

IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_qwen__qwen3-tts-12hz-1.7b-base:v1.0}"
BASE_IMAGE="${BASE_IMAGE:-docker.v2.aispeech.com/sjtu/sjtu_yukai-grace-pytorch:pytorch_2.8.0_cuda12.8_cudnn9_devel}"
BUILD_LOG="${BUILD_LOG:-${ARTIFACTS_DIR}/docker_build.log}"

: > "${BUILD_LOG}"
{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "image=${IMAGE_TAG}"
  echo "base_image=${BASE_IMAGE}"
  echo "context=${REPO_ROOT}"
  echo "note=Weights, caches, fixtures, and artifacts are not baked into the image; docker_validate.sh mounts them."
} | tee -a "${BUILD_LOG}"

docker build \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  -f "${SCRIPT_DIR}/Dockerfile" \
  -t "${IMAGE_TAG}" \
  "${REPO_ROOT}" 2>&1 | tee -a "${BUILD_LOG}"

printf '%s\n' "${IMAGE_TAG}" > "${ARTIFACTS_DIR}/docker_image_tag.txt"
docker image inspect "${IMAGE_TAG}" > "${ARTIFACTS_DIR}/docker_image_inspect.json"
echo "${IMAGE_TAG}"
