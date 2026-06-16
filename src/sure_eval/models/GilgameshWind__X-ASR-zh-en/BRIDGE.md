# XForge -> SURE Tool-Agent Boundary

This directory onboards Hugging Face model `GilgameshWind/X-ASR-zh-en`
as a SURE ASR model.

The intended flow is:

```text
download_agent / xforge
  -> discover and fetch model source/cache/weights
  -> write handoff and weight-location evidence
  -> stop

SURE model tool-agent
  -> read docs/agents/model_tool_agent/AGENTS.md
  -> create/update wrapper, local environment, and validation artifacts
```

## Download Scope

Only the deployment-ready sherpa-onnx model files are required for SURE smoke:

- `deployment/models/chunk-160ms-model/`
- `deployment/models/chunk-480ms-model/`
- `deployment/models/chunk-960ms-model/`
- `deployment/models/chunk-1920ms-model/`
- `README.md`
- `config.json`

The full HF snapshot also contains demo videos and desktop app packages. Those
are not needed for SURE evaluation and should not be re-downloaded unless a user
explicitly asks for the full artifact page.

## Current Runtime

The wrapper uses `sherpa-onnx` and defaults to:

- chunk: `960`
- provider: `cuda`; local uv must fail if it falls back to CPU
- sample rate: `16000`
- decoding method: `greedy_search`
- tail padding: `1.0s` silence before `input_finished()`

Override with:

```bash
X_ASR_CHUNK=1920 SHERPA_ONNX_PROVIDER=cuda ./docker_validate.sh
```

The observed PyPI `sherpa-onnx==1.13.2` wheel on this host is CPU-only:
requesting `SHERPA_ONNX_PROVIDER=cuda` prints `Available providers:
CPUExecutionProvider` and falls back to CPU. `local_uv_validate.sh` treats that
as a failure, not a pass.

Fresh status as of 2026-06-15:

- Docker GPU validation passes with
  `docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_x_asr_zh_en:v1.0`.
- The pushed registry digest is
  `sha256:77a9d83141cec4c79880cb5be3a92668746327dd1b7f1a3d19e48fc1be8e1ad5`.
- Container validation reports `SHERPA_ONNX_VERSION 1.13.2+cuda`.
- Local uv GPU is still blocked by host constraints: source build needs Python
  3.11 development headers, and the Docker-built extension cannot be copied
  back because it requires a newer glibc (`GLIBC_2.29`) than this host.

See `artifacts/gpu_status.json` for the exact commands and evidence logs.

## Network Rule

For Hugging Face mirror download:

```bash
. /hpc_stor03/sjtu_home/junhao.du/.local/bin/ssr-off
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 <download command>
```

Do not use proxy with `hf-mirror.com`.
