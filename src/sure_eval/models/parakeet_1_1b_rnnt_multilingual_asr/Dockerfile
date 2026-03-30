FROM python:3.10-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY pyproject.toml /workspace/pyproject.toml

RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir "nemo-toolkit[asr]>=2.0" torch torchaudio soundfile

CMD ["python", "-c", "print('Container scaffold only. Runtime blocked pending exact checkpoint evidence.')"]
