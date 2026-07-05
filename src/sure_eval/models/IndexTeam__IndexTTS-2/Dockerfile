ARG BASE_IMAGE=docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_tts_indextts2:v1.0
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    DEVICE=cuda:0

WORKDIR /workspace/sure-eval/src/sure_eval/models_reonboard/runs/IndexTeam__IndexTTS-2

RUN python - <<'PY'
import torch
print("torch", torch.__version__, "cuda", torch.version.cuda)
PY

CMD ["python", "validate.py"]
