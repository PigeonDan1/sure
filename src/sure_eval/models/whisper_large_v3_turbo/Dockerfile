ARG BASE_IMAGE=docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_whisper_large_v3_turbo:v1.0
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODEL_RUN_DIR=/workspace/sure-eval/src/sure_eval/models_reonboard/runs/whisper_large_v3_turbo \
    WHISPER_DOWNLOAD_ROOT=/model_weights/whisper \
    MODEL_ID=turbo \
    DEVICE=auto

WORKDIR ${MODEL_RUN_DIR}

RUN python -c "import torch, whisper; print(torch.__version__, torch.cuda.is_available())"

CMD ["python", "validate.py"]
