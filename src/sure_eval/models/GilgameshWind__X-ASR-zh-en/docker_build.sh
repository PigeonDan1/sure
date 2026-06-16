#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
BASE_IMAGE="${BASE_IMAGE:-pytorch/pytorch:2.4.1-cuda12.1-cudnn9-devel}"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_x_asr_zh_en:v1.0}"
SHERPA_ONNX_REF="${SHERPA_ONNX_REF:-v1.13.2}"

cd "${REPO_ROOT}"

DOCKER_BUILDKIT=1 docker build \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --build-arg "SHERPA_ONNX_REF=${SHERPA_ONNX_REF}" \
  --build-arg "HTTP_PROXY=${HTTP_PROXY:-}" \
  --build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}" \
  --build-arg "http_proxy=${http_proxy:-}" \
  --build-arg "https_proxy=${https_proxy:-}" \
  --build-arg "ALL_PROXY=${ALL_PROXY:-}" \
  --build-arg "all_proxy=${all_proxy:-}" \
  -f src/sure_eval/models/GilgameshWind__X-ASR-zh-en/Dockerfile \
  -t "${IMAGE_TAG}" \
  .

docker image inspect "${IMAGE_TAG}" >/dev/null
echo "${IMAGE_TAG}"
