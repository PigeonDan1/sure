#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_whisper_large_v3_turbo:v1.0}"
BASE_IMAGE="${BASE_IMAGE:-pytorch/pytorch:2.4.1-cuda12.1-cudnn9-devel}"

cd "${REPO_ROOT}"

run_docker_checked() {
  local output_file
  local status

  output_file="$(mktemp)"
  set +e
  "$@" 2>&1 | tee "${output_file}"
  status=${PIPESTATUS[0]}
  set -e

  if [ "${status}" -ne 0 ] || grep -Eqi '^(ERROR:|Error:)|failed to solve|No such image' "${output_file}"; then
    rm -f "${output_file}"
    return 1
  fi
  rm -f "${output_file}"
}

run_docker_checked docker build \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  -f src/sure_eval/models/whisper_large_v3_turbo/Dockerfile \
  -t "${IMAGE_TAG}" \
  .

run_docker_checked docker image inspect "${IMAGE_TAG}" >/dev/null

echo "${IMAGE_TAG}"
