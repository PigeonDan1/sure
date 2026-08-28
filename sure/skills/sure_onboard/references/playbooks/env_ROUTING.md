# Environment Playbook Routing

This file controls which environment-support memory the model tool-agent should
read. Do not load every environment playbook by default.

## Inputs

Route from:

- `MODEL_INPUT.deployment_type`
- `MODEL_INPUT.environment_hint.preferred_backend`
- detected repo files: `pyproject.toml`, `requirements.txt`, `environment.yml`,
  `pixi.toml`, `Dockerfile`, setup scripts
- `backend_choice.json` once the backend has been selected

## Default Rule

Always read:

- `docs/agents/model_tool_agent/policies/backend_selection.md`
- this routing file

Then read only the selected environment playbook.

If the backend is not selected yet, read the minimum evidence needed to select
it. Do not load all backend playbooks as a substitute for classification.

## Route Table

| Signal | Read | Notes |
|--------|------|-------|
| `deployment_type: api`, API-only model, remote endpoint/token required | `playbooks/model_api.md` | Skip local env docs unless a local client package must be built. |
| `backend: uv`, `pyproject.toml`, Python package with manageable deps | `playbooks/env_uv.md` | Preferred for pure Python local wrappers. |
| `backend: pip`, `requirements.txt`, no lockfile/tooling | `playbooks/env_pip.md` | Prefer uv as the installer if available, but keep pip-specific context small. |
| `backend: conda`, `environment.yml` | `playbooks/env_conda.md` | Record env name, channels, CUDA/PyTorch package choices. |
| `backend: pixi`, `pixi.toml` | `playbooks/env_pixi.md` | Use pixi lockfile and `pixi run` execution pattern. |
| `backend: docker`, a Docker package profile, complex CUDA/C++/system deps, upstream Dockerfile | `playbooks/env_docker.md` | Use when container delivery is selected or Python-only delivery is impractical. |
| XForge-generated scaffold | `playbooks/xforge_sure_bridge.md` plus selected backend playbook | Only when the input comes from XForge bridge workflow. |

## Escalation Rules

Read an additional environment playbook only when a concrete signal appears:

- Docker fallback after uv/conda failure: add `playbooks/env_docker.md`.
- API wrapper with local package install: add `playbooks/env_uv.md`.
- Conda environment converted to pixi: read `playbooks/env_conda.md` for source
  evidence and `playbooks/env_pixi.md` for execution.

Do not read `preflight_checklist.md` by default for every model. Read it when:

- backend selection depends on host capabilities;
- GPU, disk, Docker, registry, network, or TMPDIR risk is relevant;
- previous build or validation failed due to environment setup.

## Required Audit Record

Record the selected environment playbooks in one of:

- `artifacts/backend_choice.json`
- `artifacts/build_plan.json`
- `artifacts/tool_agent_run_report.json`

Use this shape:

```json
{
  "context_selection": {
    "environment_playbooks_read": [
      "docs/agents/model_tool_agent/playbooks/env_uv.md"
    ],
    "environment_playbooks_skipped": [
      "docs/agents/model_tool_agent/playbooks/env_docker.md"
    ],
    "reason": "backend_choice.chosen_backend is uv"
  }
}
```
