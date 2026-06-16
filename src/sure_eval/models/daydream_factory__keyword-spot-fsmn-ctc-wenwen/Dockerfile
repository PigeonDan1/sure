ARG BASE_IMAGE=docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_kws_fsmn:v1.0
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    KWS_MODEL_ROOT=/workspace/sure-eval/src/sure_eval/models/daydream_factory__keyword-spot-fsmn-ctc-wenwen \
    VIRTUAL_ENV=/opt/kws_wenwen_venv \
    PATH="/opt/kws_wenwen_venv/bin:${PATH}" \
    WEKWS_GPU=0 \
    WEKWS_KEYWORDS="你好问问,嗨小问" \
    WEKWS_THRESHOLD=0.0

RUN --mount=type=cache,target=/var/cache/apt \
    find /etc/apt/sources.list.d -type f -name '*cuda*' -exec mv {} {}.disabled \; \
    && \
    sed -i 's@/archive.ubuntu.com/@/mirrors.aliyun.com/@g; s@/security.ubuntu.com/@/mirrors.aliyun.com/@g' /etc/apt/sources.list \
    && apt-get clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/sure-eval

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv --system-site-packages "${VIRTUAL_ENV}" \
    && python -m pip install --upgrade pip wheel setuptools \
    && python -m pip install \
        "numpy<2" \
        pyyaml \
        librosa \
        soundfile \
        scipy \
        tqdm \
        modelscope \
    && python -c "import torch, torchaudio; print(torch.__version__, torchaudio.__version__, torch.cuda.is_available())"

RUN mkdir -p "${KWS_MODEL_ROOT}/.runtime/modelscope_cache" "${KWS_MODEL_ROOT}/artifacts" \
    && chmod a+rwx "${SURE_EVAL_ROOT}" \
    && chmod -R a+rwx "${KWS_MODEL_ROOT}/.runtime" "${KWS_MODEL_ROOT}/artifacts"

WORKDIR /workspace/sure-eval/src/sure_eval/models/daydream_factory__keyword-spot-fsmn-ctc-wenwen

CMD ["/opt/kws_wenwen_venv/bin/python", "validate.py"]
