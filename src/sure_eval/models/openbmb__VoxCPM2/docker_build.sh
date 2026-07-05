#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPO_ROOT="$(readlink -f "${REPO_ROOT}")"

DEFAULT_IMAGE_TAG="docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_openbmb__voxcpm2:v1.0"
IMAGE_TAG="${IMAGE_TAG:-${DEFAULT_IMAGE_TAG}}"
BASE_IMAGE="${BASE_IMAGE:-pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime}"
LOG_PATH="${LOG_PATH:-${SCRIPT_DIR}/artifacts/docker_build.log}"
TAG_PATH="${TAG_PATH:-${SCRIPT_DIR}/artifacts/docker_image_tag.txt}"

mkdir -p "${SCRIPT_DIR}/artifacts"

{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "repo_root=${REPO_ROOT}"
  echo "model_dir=${SCRIPT_DIR}"
  echo "image_tag=${IMAGE_TAG}"
  echo "base_image=${BASE_IMAGE}"
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
    docker build \
      --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
      -f "${SCRIPT_DIR}/Dockerfile" \
      -t "${IMAGE_TAG}" \
      "${SCRIPT_DIR}"
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
    docker image inspect "${IMAGE_TAG}" >/dev/null
  printf '%s\n' "${IMAGE_TAG}" > "${TAG_PATH}"
  echo "docker_build_status=passed"
  echo "docker_image_tag_file=${TAG_PATH}"
} 2>&1 | tee "${LOG_PATH}"
