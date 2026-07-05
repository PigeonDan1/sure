FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    MODEL_DIR=/workspace/sure-eval/src/sure_eval/models/nvidia__parakeet-rnnt-1.1b \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/parakeet_rnnt_venv
ENV PATH=/opt/parakeet_rnnt_venv/bin:$PATH

COPY src/sure_eval/models/nvidia__parakeet-rnnt-1.1b/requirements-phase1.txt /tmp/requirements-phase1.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -i https://pypi.tuna.tsinghua.edu.cn/simple torch torchaudio && \
    pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
      huggingface_hub modelscope "nemo_toolkit[asr]" soundfile

WORKDIR ${MODEL_DIR}
CMD ["/opt/parakeet_rnnt_venv/bin/python", "validate.py"]
