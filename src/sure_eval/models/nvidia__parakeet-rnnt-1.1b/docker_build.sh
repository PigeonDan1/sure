#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${MODEL_DIR}/../../../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_nvidia__parakeet-rnnt-1.1b:v1.0}"

mkdir -p "${MODEL_DIR}/artifacts"
echo "${IMAGE_TAG}" > "${MODEL_DIR}/artifacts/docker_image_tag.txt"

docker build \
  -f "${MODEL_DIR}/Dockerfile" \
  -t "${IMAGE_TAG}" \
  "${REPO_ROOT}" \
  2>&1 | tee "${MODEL_DIR}/artifacts/docker_build.log"

docker image inspect "${IMAGE_TAG}" >/dev/null
