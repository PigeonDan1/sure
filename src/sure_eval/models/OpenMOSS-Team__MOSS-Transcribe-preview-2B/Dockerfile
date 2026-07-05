ARG BASE_IMAGE=docker.v2.aispeech.com/sjtu/sjtu_yukai-grace-asr-tts-cuda-notconda:v0.18.grpo.5090.torch28.transformers4571
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SURE_EVAL_ROOT=/workspace/sure-eval \
    MOSS_MODEL_ROOT=/workspace/sure-eval/src/sure_eval/models/OpenMOSS-Team__MOSS-Transcribe-preview-2B \
    HF_HOME=/workspace/sure-eval/src/sure_eval/models/OpenMOSS-Team__MOSS-Transcribe-preview-2B/.runtime/hf-home \
    HF_HUB_CACHE=/workspace/sure-eval/src/sure_eval/models/OpenMOSS-Team__MOSS-Transcribe-preview-2B/.runtime/hf-home/hub \
    TRANSFORMERS_CACHE=/workspace/sure-eval/src/sure_eval/models/OpenMOSS-Team__MOSS-Transcribe-preview-2B/.runtime/hf-home/hub \
    MPLCONFIGDIR=/workspace/sure-eval/src/sure_eval/models/OpenMOSS-Team__MOSS-Transcribe-preview-2B/.runtime/matplotlib \
    TMPDIR=/workspace/sure-eval/src/sure_eval/models/OpenMOSS-Team__MOSS-Transcribe-preview-2B/.runtime/tmp \
    DEVICE=cuda:0

WORKDIR /workspace/sure-eval

RUN python - <<'PY'
import importlib
required = ["torch", "transformers", "huggingface_hub", "librosa", "soundfile", "safetensors", "numpy"]
for name in required:
    module = importlib.import_module(name)
    print(name, getattr(module, "__version__", "ok"))
PY

WORKDIR /workspace/sure-eval/src/sure_eval/models/OpenMOSS-Team__MOSS-Transcribe-preview-2B
CMD ["python", "validate.py"]
