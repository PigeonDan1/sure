ARG BASE_IMAGE=pytorch/pytorch:2.4.1-cuda12.1-cudnn9-devel
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    WHISPER_ROOT=/workspace/sure-eval/src/sure_eval/models/whisper_large_v3_turbo \
    MODEL_ID=turbo \
    WHISPER_DOWNLOAD_ROOT=/workspace/sure-eval/src/sure_eval/models/whisper_large_v3_turbo/checkpoints \
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
RUN pip install \
        "openai-whisper" \
        "modelscope>=1.20.0" \
        "imageio-ffmpeg" \
        "jiwer>=3.0.0" \
    && python -c "import torch; print('torch:', torch.__version__)" \
    && python -c "import whisper; print('whisper ok')"

RUN mkdir -p "${WHISPER_ROOT}/artifacts" "${WHISPER_ROOT}/docker_artifacts" \
    && chmod a+rwx "${SURE_EVAL_ROOT}" \
    && chmod -R a+rwx "${WHISPER_ROOT}/artifacts" "${WHISPER_ROOT}/docker_artifacts"

WORKDIR /workspace/sure-eval/src/sure_eval/models/whisper_large_v3_turbo

CMD ["python", "validate.py"]
