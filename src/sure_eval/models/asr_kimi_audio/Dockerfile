ARG BASE_IMAGE=pytorch/pytorch:2.4.1-cuda12.1-cudnn9-devel
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG UV_VERSION=0.7.13

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_PROJECT_ENVIRONMENT=/opt/asr_kimi_audio_venv \
    VIRTUAL_ENV=/opt/asr_kimi_audio_venv \
    PATH="/opt/asr_kimi_audio_venv/bin:/root/.local/bin:${PATH}" \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    ASR_KIMI_AUDIO_ROOT=/workspace/sure-eval/src/sure_eval/models/asr_kimi_audio \
    KIMI_AUDIO_MODEL_PATH=/workspace/sure-eval/src/sure_eval/models/asr_kimi_audio/.runtime/modelscope_cache/models/moonshotai/Kimi-Audio-7B-Instruct \
    MODELSCOPE_CACHE=/workspace/sure-eval/src/sure_eval/models/asr_kimi_audio/.runtime/modelscope_cache \
    HF_HOME=/workspace/sure-eval/src/sure_eval/models/asr_kimi_audio/.runtime/huggingface \
    HF_HUB_CACHE=/workspace/sure-eval/src/sure_eval/models/asr_kimi_audio/.runtime/huggingface/hub \
    DEVICE=auto

RUN --mount=type=cache,target=/var/cache/apt \
    sed -i 's@/archive.ubuntu.com/@/mirrors.aliyun.com/@g; s@/security.ubuntu.com/@/mirrors.aliyun.com/@g' /etc/apt/sources.list \
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

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip wheel setuptools \
    && python -m pip install "uv==${UV_VERSION}"

COPY pyproject.toml requirements.txt ./
COPY src/sure_eval ./src/sure_eval

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv --system-site-packages "${VIRTUAL_ENV}" \
    && python -m pip install --upgrade pip wheel setuptools \
    && python -m pip install -r requirements.txt \
    && python -m pip install --no-deps -e . \
    && python -m pip install \
        "torch==2.4.1" \
        "torchaudio==2.4.1" \
        "torchvision==0.19.1" \
        "transformers==4.57.6" \
        "accelerate==1.13.0" \
        "modelscope>=1.10.0" \
        "soundfile>=0.12.0" \
        "numpy" \
        "librosa==0.11.0" \
        "loguru" \
        "huggingface-hub==0.36.2" \
        "safetensors" \
        "tiktoken" \
        "omegaconf==2.3.0" \
        "hyperpyyaml" \
        "easydict" \
        "fire" \
        "ujson" \
        "jsonlines" \
        "datasets" \
        "diffusers" \
        "einops" \
        "bitsandbytes" \
        "sentencepiece" \
        "decord" \
        "timm" \
        "pytorch-lightning" \
        "conformer" \
        "openai-whisper" \
    && python -c "import torch, torchaudio; print(torch.__version__, torchaudio.__version__)"

RUN mkdir -p "${MODELSCOPE_CACHE}" "${HF_HUB_CACHE}" "${ASR_KIMI_AUDIO_ROOT}/artifacts" \
    && chmod a+rwx "${SURE_EVAL_ROOT}" \
    && chmod -R a+rwx "${ASR_KIMI_AUDIO_ROOT}/.runtime" "${ASR_KIMI_AUDIO_ROOT}/artifacts"

WORKDIR /workspace/sure-eval/src/sure_eval/models/asr_kimi_audio

CMD ["/opt/asr_kimi_audio_venv/bin/python", "validate.py"]
