# XForge -> SURE Tool-Agent Boundary

This directory is the SURE model onboarding result for `Plachtaa/seed-vc`.
The intended flow is:

```text
download_agent / xforge
  -> discover and fetch model source, runtime cache, and weights
  -> write a handoff plus weight-location evidence
  -> stop

SURE model tool-agent
  -> read docs/agents/model_tool_agent/AGENTS.md
  -> create/update the SURE wrapper, local environment, Docker runtime, and validation artifacts
  -> run import/load/infer/contract validation
```

## XForge-Owned Handoff Files

These files represent the download/discovery side of the bridge:

- `artifacts/xforge_sure_handoff.json`
- `artifacts/weights_manifest.json`
- `.runtime/source/seed-vc/`
- `.runtime/huggingface/`

XForge should not generate model wrappers, Docker scripts, or SURE validation
logic directly. It should hand off enough evidence for the SURE model tool-agent
to continue.

## SURE Tool-Agent-Owned Files

These files are owned by the SURE model onboarding workflow:

- `model.spec.yaml`
- `model.py`
- `server.py`
- `validate.py`
- `config.yaml`
- `local_uv_setup.sh`
- `local_uv_validate.sh`
- `Dockerfile`
- `docker_build.sh`
- `docker_validate.sh`
- `artifacts/backend_choice.json`
- `artifacts/build_plan.json`
- `artifacts/spec_validation.json`
- `artifacts/preflight_summary.json`
- `artifacts/docker_validation.json`
- `artifacts/verdict.json`
- `artifacts/artifact_manifest.json`

## Dependency Files

Keep only two dependency entrypoints:

- `requirements-core.txt`: local uv debugging and smoke validation.
- `requirements-docker.txt`: Docker build dependencies excluding torch,
  torchaudio, and torchvision because the Docker base image already provides
  CUDA torch.

Do not add `requirements-local.txt`, `requirements-full.txt`, or
`requirements-xforge.txt` unless a new backend genuinely needs them and the
reason is recorded in `artifacts/build_plan.json`.

## Weight Policy

Do not bake weights into Docker images.

- Provider/runtime caches stay under `.runtime/`.
- `checkpoints/` is only for explicit local weights and may remain empty.
- Docker validation mounts `.runtime/` and `checkpoints/` from the host.

## Current Validated Entrypoints

```bash
src/sure_eval/models/Plachtaa__seed-vc/local_uv_validate.sh
src/sure_eval/models/Plachtaa__seed-vc/docker_build.sh
src/sure_eval/models/Plachtaa__seed-vc/docker_validate.sh
```

For HuggingFace mirror access, first disable proxy and then run with
`HF_ENDPOINT=https://hf-mirror.com`. Do not use proxy for hf-mirror downloads.
