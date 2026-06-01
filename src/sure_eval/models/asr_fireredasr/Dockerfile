ARG BASE_IMAGE=pytorch/pytorch:2.4.1-cuda12.1-cudnn9-devel
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG UV_VERSION=0.7.13

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_PROJECT_ENVIRONMENT=/opt/asr_fireredasr_venv \
    VIRTUAL_ENV=/opt/asr_fireredasr_venv \
    PATH="/opt/asr_fireredasr_venv/bin:/root/.local/bin:${PATH}" \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    ASR_FIREREDASR_ROOT=/workspace/sure-eval/src/sure_eval/models/asr_fireredasr \
    FIREREDASR_MODEL_PATH=/workspace/sure-eval/src/sure_eval/models/asr_fireredasr/.runtime/modelscope_cache/models/FireRedTeam/FireRedASR-LLM-L \
    LLM_PATH=/workspace/sure-eval/src/sure_eval/models/asr_fireredasr/.runtime/modelscope_cache/models/Qwen/Qwen2-7B-Instruct \
    MODELSCOPE_CACHE=/workspace/sure-eval/src/sure_eval/models/asr_fireredasr/.runtime/modelscope_cache \
    DEVICE=cuda

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
        "transformers>=4.40.0" \
        "modelscope>=1.10.0" \
        "soundfile>=0.12.0" \
        "numpy<2" \
        "kaldiio" \
        "kaldi-native-fbank" \
        "sentencepiece" \
        "peft" \
        "accelerate" \
        "protobuf" \
    && python -c "import torch; print(torch.__version__)"

RUN mkdir -p "${ASR_FIREREDASR_ROOT}/.runtime/modelscope_cache" "${ASR_FIREREDASR_ROOT}/artifacts" \
    && chmod a+rwx "${SURE_EVAL_ROOT}" \
    && chmod -R a+rwx "${ASR_FIREREDASR_ROOT}/.runtime" "${ASR_FIREREDASR_ROOT}/artifacts"

WORKDIR /workspace/sure-eval/src/sure_eval/models/asr_fireredasr

CMD ["/opt/asr_fireredasr_venv/bin/python", "validate.py"]
