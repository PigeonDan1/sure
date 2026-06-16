#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [ ! -x ".venv/bin/python" ]; then
  echo "Missing .venv/bin/python. Run local_uv_setup.sh first." >&2
  exit 2
fi

mkdir -p artifacts
run_log="artifacts/local_uv_validate.stdout.log"
rm -f "${run_log}"

SITE_PACKAGES="$("${SCRIPT_DIR}/.venv/bin/python" - <<'PY'
import sysconfig
print(sysconfig.get_path("platlib"))
PY
)"

SURE_XFORGE_STATIC_ONLY="${SURE_XFORGE_STATIC_ONLY:-0}" \
SHERPA_ONNX_PROVIDER="${SHERPA_ONNX_PROVIDER:-cuda}" \
X_ASR_TAIL_PADDING_SECONDS="${X_ASR_TAIL_PADDING_SECONDS:-1.0}" \
LD_LIBRARY_PATH="${SITE_PACKAGES}/nvidia/cudnn/lib:${SITE_PACKAGES}/nvidia/cublas/lib:${SITE_PACKAGES}/nvidia/cuda_nvrtc/lib:/usr/local/cuda-12.2/targets/x86_64-linux/lib:${LD_LIBRARY_PATH:-}" \
.venv/bin/python validate.py 2>&1 | tee "${run_log}"

if grep -q "Fallback to cpu" "${run_log}"; then
  echo "Requested CUDA provider but sherpa-onnx fell back to CPU." >&2
  exit 5
fi
