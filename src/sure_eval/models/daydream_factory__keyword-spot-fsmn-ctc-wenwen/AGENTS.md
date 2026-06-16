# Agent Notes for KWS Wenwen Onboarding

This file is local to
`src/sure_eval/models/daydream_factory__keyword-spot-fsmn-ctc-wenwen`.
Use it together with the parent model onboarding contract in
`docs/agents/model_tool_agent/AGENTS.md`.

## Current Status

- Task: KWS, keyword spotting for `你好问问,嗨小问`.
- Source toolkit: WekWS.
- Weights: ModelScope `daydream-factory/keyword-spot-fsmn-ctc-wenwen`.
- Weight location:
  `.runtime/modelscope_cache/daydream-factory/keyword-spot-fsmn-ctc-wenwen`.
- `checkpoints/` should remain empty unless a converted local-only artifact is
  explicitly produced.
- Local uv validation has passed with `WEKWS_GPU=0`, `gpu: 0`, and
  `cuda_available: true` in `artifacts/verdict.json`.

## Required Execution Rules

1. Prefer GPU. Run validation with `WEKWS_GPU=0` when a local CUDA GPU is
   visible. CPU is only a fallback when CUDA is unavailable.
2. On AISpeech debug machines, clear proxy variables for local Docker/GPU
   commands:
   `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy ...`.
3. Do not use the repository root as the Docker build context for this model.
   The first attempt sent 24.5GB of context and was terminated. Build from this
   model directory with `docker_build.sh`.
4. Do not bake weights, fixture data, `.runtime`, or `.venv` into the Docker
   image. Docker should contain the runtime environment only; code, WekWS
   source, weights, fixtures, and artifacts are mounted by `docker_validate.sh`.
5. Reuse local base image
   `docker.v2.aispeech.com/sjtu/sjtu_yukai-dujunhao-sure_kws_fsmn:v1.0`
   unless there is a concrete incompatibility. Do not pull external PyTorch
   images just to build this model.

## Validation Commands

Local uv validation:

```bash
WEKWS_GPU=0 src/sure_eval/models/daydream_factory__keyword-spot-fsmn-ctc-wenwen/local_uv_validate.sh
```

Docker build:

```bash
src/sure_eval/models/daydream_factory__keyword-spot-fsmn-ctc-wenwen/docker_build.sh
```

Docker validation:

```bash
src/sure_eval/models/daydream_factory__keyword-spot-fsmn-ctc-wenwen/docker_validate.sh
```

Expected local validation output:

- Positive fixture detects `嗨小问`.
- Negative fixture is rejected.
- `artifacts/verdict.json` has `status: passed`, `gpu: 0`, and
  `cuda_available: true` when run outside the sandbox with CUDA visible.

## Important Implementation Notes

- `predict()` intentionally uses offline CTC prefix beam search for stable
  fixture validation.
- `predict_streaming()` keeps the upstream streaming path available for later
  runtime experiments.
- The model-local WekWS `tools/make_list.py` is patched to apply the upstream
  Mobvoi recipe token id shift (`tokens.txt` ids become `id - 1`). Do not remove
  this patch; otherwise the positive fixture no longer decodes as `嗨小问`.
