#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${MODEL_DIR}"

mkdir -p artifacts .runtime/uv-cache .runtime/uv-python .runtime/hf-home .runtime/nemo-cache .runtime/tmp .runtime/matplotlib
export UV_CACHE_DIR="${MODEL_DIR}/.runtime/uv-cache"
export UV_PYTHON_INSTALL_DIR="${MODEL_DIR}/.runtime/uv-python"
export UV_LINK_MODE=copy
export TMPDIR="${MODEL_DIR}/.runtime/tmp"
export MPLCONFIGDIR="${MODEL_DIR}/.runtime/matplotlib"

LOG="${MODEL_DIR}/artifacts/build.log"
: > "${LOG}"
{
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "model_dir=${MODEL_DIR}"
  echo "uv=$(command -v uv)"
  uv --version
  echo "python3.12=$(command -v python3.12)"
  python3.12 --version

  uv venv --python python3.12 .runtime/.venv
  .runtime/.venv/bin/python -m ensurepip --upgrade || true
  uv pip install --python .runtime/.venv/bin/python --upgrade pip setuptools wheel

  uv pip install --python .runtime/.venv/bin/python \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    torch torchaudio

  uv pip install --python .runtime/.venv/bin/python \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    "huggingface_hub>=0.23.0" "modelscope>=1.27.0" "nemo_toolkit[asr]" "soundfile>=0.12.1"

  .runtime/.venv/bin/python --version
  .runtime/.venv/bin/python -c "import torch, torchaudio; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'cuda_available', torch.cuda.is_available()); print('torchaudio', torchaudio.__version__)"
  .runtime/.venv/bin/python -c "import nemo.collections.asr as nemo_asr; print('nemo_asr_import_ok', nemo_asr.__name__)"
  uv pip freeze --python .runtime/.venv/bin/python > requirements.lock
} 2>&1 | tee -a "${LOG}"
