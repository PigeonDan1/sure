#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-reonboard_asr_sensevoice_small:v1.0}"
BASE_IMAGE="${BASE_IMAGE:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_sensevoice_small:v1.0}"

cd "${REPO_ROOT}"
docker build \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  -f src/sure_eval/models_reonboard/runs/asr_sensevoice_small/Dockerfile \
  -t "${IMAGE_TAG}" \
  .
docker image inspect "${IMAGE_TAG}" >/dev/null
echo "${IMAGE_TAG}"
