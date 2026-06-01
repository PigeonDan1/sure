ARG BASE_IMAGE=pytorch/pytorch:2.4.1-cuda12.1-cudnn9-devel
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG UV_VERSION=0.7.13

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/opt/asr_qwen3_venv \
    VIRTUAL_ENV=/opt/asr_qwen3_venv \
    PATH="/opt/asr_qwen3_venv/bin:/root/.local/bin:${PATH}" \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    ASR_QWEN3_ROOT=/workspace/sure-eval/src/sure_eval/models/asr_qwen3 \
    MODEL_PATH=Qwen/Qwen3-ASR-1.7B \
    MODELSCOPE_CACHE=/workspace/sure-eval/src/sure_eval/models/asr_qwen3/.runtime/modelscope_cache \
    HF_HOME=/workspace/sure-eval/src/sure_eval/models/asr_qwen3/.runtime/huggingface \
    HF_HUB_CACHE=/workspace/sure-eval/src/sure_eval/models/asr_qwen3/.runtime/huggingface/hub \
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

RUN python -m pip install --upgrade pip \
    && python -m pip install "uv==${UV_VERSION}"

COPY pyproject.toml uv.lock requirements.txt ./
COPY src/sure_eval ./src/sure_eval

RUN python -m venv --system-site-packages "${VIRTUAL_ENV}" \
    && python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m pip install --no-deps -e . \
    && python -m pip install \
        "transformers==4.57.6" \
        "accelerate==1.12.0" \
        "soundfile>=0.12.0" \
        "scipy>=1.10.0" \
        "pyyaml>=6.0.0" \
        "av>=17.0.0" \
        "sox>=1.5.0" \
        "dynet38>=2.2" \
        "nagisa>=0.2.11" \
        "soynlp>=0.0.493" \
    && python -m pip install --no-deps "qwen-omni-utils==0.0.9" "qwen-asr==0.0.6" \
    && python -c "import torch; assert torch.__version__.startswith('2.4.1'), torch.__version__"

RUN mkdir -p "${MODELSCOPE_CACHE}" "${HF_HUB_CACHE}" "${ASR_QWEN3_ROOT}/artifacts" \
    && chmod a+rwx "${SURE_EVAL_ROOT}" \
    && chmod -R a+rwx "${ASR_QWEN3_ROOT}/.runtime" "${ASR_QWEN3_ROOT}/artifacts"

WORKDIR /workspace/sure-eval/src/sure_eval/models/asr_qwen3

CMD ["/opt/asr_qwen3_venv/bin/python", "validate.py"]
