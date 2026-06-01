#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
BASE_IMAGE="${BASE_IMAGE:-pytorch/pytorch:2.4.1-cuda12.1-cudnn9-devel}"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_sensevoice_small:v1.0}"

cd "${REPO_ROOT}"

DOCKER_BUILDKIT=1 docker build \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  -f src/sure_eval/models/asr_sensevoice_small/Dockerfile \
  -t "${IMAGE_TAG}" \
  .

echo "${IMAGE_TAG}"
