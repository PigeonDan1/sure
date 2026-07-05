ARG BASE_IMAGE=docker.v2.aispeech.com/sjtu/sjtu_yukai-grace-pytorch:pytorch_2.8.0_cuda12.8_cudnn9_devel
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/qwen3_tts_12hz_1_7b_base_venv \
    PATH="/opt/qwen3_tts_12hz_1_7b_base_venv/bin:/opt/conda/bin:${PATH}" \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    MODEL_DIR=/workspace/sure-eval/src/sure_eval/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base \
    MODEL_PATH=/workspace/sure-eval/src/sure_eval/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/checkpoints/Qwen3-TTS-12Hz-1.7B-Base \
    HF_HOME=/workspace/sure-eval/src/sure_eval/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/.runtime/huggingface \
    HF_HUB_CACHE=/workspace/sure-eval/src/sure_eval/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/.runtime/huggingface/hub \
    MODELSCOPE_CACHE=/workspace/sure-eval/src/sure_eval/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/.runtime/modelscope_cache \
    MPLCONFIGDIR=/workspace/sure-eval/src/sure_eval/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/.runtime/matplotlib \
    TMPDIR=/workspace/sure-eval/src/sure_eval/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/.runtime/tmp \
    DEVICE_MAP=cuda:0 \
    TORCH_DTYPE=bfloat16 \
    ATTN_IMPLEMENTATION=eager

WORKDIR /workspace/sure-eval

COPY src/sure_eval/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base/requirements-local.txt /tmp/qwen3_tts_requirements.txt

RUN python -m venv --system-site-packages "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/python" -m pip install --upgrade pip \
    && "${VIRTUAL_ENV}/bin/python" -m pip install -r /tmp/qwen3_tts_requirements.txt \
    && "${VIRTUAL_ENV}/bin/python" -m pip check

RUN mkdir -p "${MODEL_DIR}/artifacts" "${MODEL_DIR}/.runtime" "${MODEL_DIR}/checkpoints" \
    && chmod -R a+rwx /workspace/sure-eval

WORKDIR /workspace/sure-eval/src/sure_eval/models/Qwen__Qwen3-TTS-12Hz-1.7B-Base

CMD ["/opt/qwen3_tts_12hz_1_7b_base_venv/bin/python", "validate.py"]
