ARG BASE_IMAGE=docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-f5tts:v1.0
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    DEVICE=cuda:0 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /workspace/sure-eval/src/sure_eval/models_reonboard/runs/SWivid__F5-TTS_Emilia-ZH-EN

RUN python - <<'PY'
import torch
from f5_tts.api import F5TTS
print("torch", torch.__version__, "cuda", torch.version.cuda)
print("f5_tts_import_ok", F5TTS)
PY

CMD ["python", "validate.py"]
