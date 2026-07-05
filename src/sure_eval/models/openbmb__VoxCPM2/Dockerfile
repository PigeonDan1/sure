ARG BASE_IMAGE=pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/VoxCPM2_venv \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    MODEL_DIR=/workspace/sure-eval/src/sure_eval/models/openbmb__VoxCPM2 \
    DEVICE=cuda:0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        git \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN /opt/conda/bin/python -m venv --system-site-packages "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/python" -m pip install --upgrade pip \
    && "${VIRTUAL_ENV}/bin/python" -m pip install \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        "numpy<2.0" \
        safetensors \
        soundfile \
        librosa \
        "transformers>=4.36.2,<5" \
        huggingface-hub \
        "modelscope>=1.22.0" \
        einops \
        "pydantic>=2" \
        tqdm \
        sentencepiece \
    && "${VIRTUAL_ENV}/bin/python" -m pip install \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        --no-deps \
        voxcpm==2.0.3

RUN mkdir -p "${MODEL_DIR}/artifacts" "${MODEL_DIR}/.runtime" \
    && chmod -R a+rwx /workspace "${VIRTUAL_ENV}"

WORKDIR /workspace/sure-eval/src/sure_eval/models/openbmb__VoxCPM2

CMD ["/opt/VoxCPM2_venv/bin/python", "validate.py"]
