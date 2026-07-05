ARG BASE_IMAGE=docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_asr_qwen3:v1.1
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    MODEL_RUN_DIR=/workspace/sure-eval/src/sure_eval/models_reonboard/runs/asr_qwen3 \
    MODELSCOPE_CACHE=/workspace/sure-eval/src/sure_eval/models_reonboard/runs/asr_qwen3/.runtime/modelscope_cache \
    MODEL_PATH=/workspace/sure-eval/src/sure_eval/models_reonboard/runs/asr_qwen3/.runtime/modelscope_cache/models/Qwen/Qwen3-ASR-1___7B \
    DEVICE=cuda \
    REQUIRE_GPU=1

WORKDIR ${MODEL_RUN_DIR}

RUN /opt/asr_qwen3_venv/bin/python -c "import torch, qwen_asr; print(torch.__version__, torch.cuda.is_available())"

CMD ["/opt/asr_qwen3_venv/bin/python", "validate.py"]
