ARG BASE_IMAGE=pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    ASR_PARAKEET_ROOT=/workspace/sure-eval/src/sure_eval/models/asr_parakeet \
    MODEL_PATH=/workspace/sure-eval/src/sure_eval/models/asr_parakeet/checkpoints/parakeet-tdt-0.6b-v2.nemo \
    DEVICE=auto

RUN sed -i 's@/archive.ubuntu.com/@/mirrors.aliyun.com/@g; s@/security.ubuntu.com/@/mirrors.aliyun.com/@g' /etc/apt/sources.list \
    && apt-get clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        libsndfile1 \
        wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/sure-eval

RUN python -m pip install --upgrade pip

COPY pyproject.toml uv.lock ./
COPY src/sure_eval ./src/sure_eval

# Install directly into the conda environment (container is already isolated).
# Do NOT upgrade torch/torchvision/torchaudio to avoid CUDA version mismatch.
RUN pip install \
        "nemo-toolkit[asr]>=2.0" \
        "soundfile>=0.12.0" \
        "numpy>=1.26.0" \
        "modelscope>=1.20.0" \
        "jiwer>=3.0.0" \
    && python -c "import torch; print('torch:', torch.__version__)" \
    && python -c "import torchvision; print('torchvision:', torchvision.__version__)" \
    && python -c "import nemo.collections.asr as nemo_asr; print('nemo asr ok')"

RUN mkdir -p "${ASR_PARAKEET_ROOT}/artifacts" "${ASR_PARAKEET_ROOT}/docker_artifacts" \
    && chmod a+rwx "${SURE_EVAL_ROOT}" \
    && chmod -R a+rwx "${ASR_PARAKEET_ROOT}/artifacts" "${ASR_PARAKEET_ROOT}/docker_artifacts"

WORKDIR /workspace/sure-eval/src/sure_eval/models/asr_parakeet

CMD ["python", "validate.py"]
