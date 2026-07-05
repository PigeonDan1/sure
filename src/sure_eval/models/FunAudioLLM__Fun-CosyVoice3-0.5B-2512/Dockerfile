ARG BASE_IMAGE=docker.v2.aispeech.com/sjtu/sjtu_yukai-zhihanli-cosyvoice3:v1.0
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEVICE=cuda:0 \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    MODEL_DIR=/workspace/sure-eval/src/sure_eval/models/FunAudioLLM__Fun-CosyVoice3-0.5B-2512 \
    PATH=/opt/conda/bin:${PATH}

WORKDIR /workspace/sure-eval/src/sure_eval/models/FunAudioLLM__Fun-CosyVoice3-0.5B-2512

RUN /opt/conda/bin/python - <<'PY'
import torch
import torchaudio
print("torch", torch.__version__, "cuda", torch.version.cuda)
print("torchaudio", torchaudio.__version__)
PY

CMD ["/opt/conda/bin/python", "validate.py"]
