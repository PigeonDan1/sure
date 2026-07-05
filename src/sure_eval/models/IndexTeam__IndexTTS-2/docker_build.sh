#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-reonboard_indextts2:v1.0}"
BASE_IMAGE="${BASE_IMAGE:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_tts_indextts2:v1.0}"

build_log="artifacts/docker_build.log"
rm -f "${build_log}"
docker build --build-arg "BASE_IMAGE=${BASE_IMAGE}" -t "${IMAGE_TAG}" -f Dockerfile . 2>&1 | tee "${build_log}"
if grep -q "Error: exit status" "${build_log}"; then
  echo "Docker wrapper reported an inner build failure." >&2
  exit 6
fi
echo "${IMAGE_TAG}" > artifacts/docker_image_tag.txt
docker image inspect "${IMAGE_TAG}" --format '{{json .Id}} {{json .Size}}' > artifacts/docker_image.inspect
echo "Built ${IMAGE_TAG}"
