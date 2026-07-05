ARG BASE_IMAGE=docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_sensevoice_small:v1.0
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    MODEL_RUN_DIR=/workspace/sure-eval/src/sure_eval/models_reonboard/runs/asr_sensevoice_small \
    SENSEVOICE_MODEL_PATH=/model_weights/SenseVoiceSmall \
    DEVICE=auto

WORKDIR ${MODEL_RUN_DIR}

RUN /opt/asr_sensevoice_small_venv/bin/python -c "import torch, funasr; print(torch.__version__, torch.cuda.is_available())"

CMD ["/opt/asr_sensevoice_small_venv/bin/python", "validate.py"]
