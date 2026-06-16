ARG BASE_IMAGE=pytorch/pytorch:2.4.1-cuda12.1-cudnn9-devel
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG UV_VERSION=0.7.13
ARG SHERPA_ONNX_REF=v1.13.2
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy
ARG ALL_PROXY
ARG all_proxy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_PROJECT_ENVIRONMENT=/opt/x_asr_zh_en_venv \
    VIRTUAL_ENV=/opt/x_asr_zh_en_venv \
    PATH="/opt/x_asr_zh_en_venv/bin:/root/.local/bin:${PATH}" \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    X_ASR_MODEL_DIR=/workspace/sure-eval/src/sure_eval/models/GilgameshWind__X-ASR-zh-en \
    X_ASR_MODEL_ROOT=/workspace/sure-eval/src/sure_eval/models/GilgameshWind__X-ASR-zh-en/.runtime/huggingface/GilgameshWind/X-ASR-zh-en \
    X_ASR_CHUNK=960 \
    X_ASR_TAIL_PADDING_SECONDS=1.0 \
    SHERPA_ONNX_PROVIDER=cuda \
    SHERPA_ONNX_NUM_THREADS=1 \
    LD_LIBRARY_PATH="/opt/conda/lib/python3.11/site-packages/nvidia/cudnn/lib:/usr/local/cuda/lib64:/usr/local/cuda/targets/x86_64-linux/lib:${LD_LIBRARY_PATH}"

ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    http_proxy=${http_proxy} \
    https_proxy=${https_proxy} \
    ALL_PROXY=${ALL_PROXY} \
    all_proxy=${all_proxy}

RUN --mount=type=cache,target=/var/cache/apt \
    sed -i 's@/archive.ubuntu.com/@/mirrors.aliyun.com/@g; s@/security.ubuntu.com/@/mirrors.aliyun.com/@g' /etc/apt/sources.list \
    && apt-get clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        ffmpeg \
        git \
        libsndfile1 \
        ninja-build \
        pkg-config \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/sure-eval

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip wheel setuptools \
    && python -m pip install "uv==${UV_VERSION}"

COPY pyproject.toml requirements.txt ./
COPY src/sure_eval ./src/sure_eval

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv --system-site-packages "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/python" -m pip install --upgrade pip wheel setuptools \
    && "${VIRTUAL_ENV}/bin/python" -m pip install --no-deps -e . \
    && "${VIRTUAL_ENV}/bin/python" -m pip install \
        "numpy" \
        "soundfile>=0.12.0" \
        "librosa>=0.10.0"

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/sherpa-onnx-build \
    cd /root/.cache/sherpa-onnx-build \
    && if [ ! -d sherpa-onnx/.git ]; then rm -rf sherpa-onnx \
       && git clone --branch "${SHERPA_ONNX_REF}" --depth 1 --recurse-submodules --shallow-submodules https://github.com/k2-fsa/sherpa-onnx.git; fi \
    && cd sherpa-onnx \
    && git checkout "${SHERPA_ONNX_REF}" \
    && git submodule update --init --recursive \
    && SHERPA_ONNX_CMAKE_ARGS="-DSHERPA_ONNX_ENABLE_GPU=ON -DSHERPA_ONNX_ENABLE_TESTS=OFF -DSHERPA_ONNX_ENABLE_BINARY=OFF" \
       "${VIRTUAL_ENV}/bin/python" -m pip install --no-build-isolation --no-deps .

RUN mkdir -p "${X_ASR_MODEL_ROOT}" "${X_ASR_MODEL_DIR}/artifacts" \
    && chmod a+rwx "${SURE_EVAL_ROOT}" \
    && chmod -R a+rwx "${X_ASR_MODEL_DIR}/.runtime" "${X_ASR_MODEL_DIR}/artifacts"

ENV HTTP_PROXY= \
    HTTPS_PROXY= \
    http_proxy= \
    https_proxy= \
    ALL_PROXY= \
    all_proxy= \
    LD_LIBRARY_PATH="/opt/conda/lib/python3.11/site-packages/nvidia/cudnn/lib:/usr/local/cuda/lib64:/usr/local/cuda/targets/x86_64-linux/lib:${LD_LIBRARY_PATH}"

WORKDIR /workspace/sure-eval/src/sure_eval/models/GilgameshWind__X-ASR-zh-en

CMD ["/opt/x_asr_zh_en_venv/bin/python", "validate.py"]
