# Backend Selection Policy

**Version**: 2.0
**Scope**: `/sure_onboard` local adaptation and runtime delivery

## Two separate decisions

`backend_choice.json.backend` selects the environment used to adapt and validate the model locally. It may be `uv`, `pip`, `conda`, `pixi`, or `docker`. `package_profile` selects the delivered Eval runtime.

For every local model, these decisions converge according to `package_profile`:

1. Adapt with the repository-compatible backend.
2. Run import, load, inference, and contract checks in that environment.
3. For `package=none`, bind the selected Python executable, lockfile hashes, server command, working directory, required imports, and tool names.
4. For `package=docker-registry`, reproduce the passing environment in an image, repeat bounded checks, push it, and pull-verify its immutable registry digest.
5. Publish exactly one Eval runtime identity in `runtime_inventory.json`.

Eval sees only `python` or `container`; `uv`, `pip`, `conda`, and `pixi` remain Onboard implementation details. The approved Python is explicit and is not a fallback to the current shell.

## Local backend routing

| Evidence | Adaptation backend |
|---|---|
| API-only model | `api`; package profile must be `none` |
| Upstream Dockerfile or complex OS/CUDA dependencies with container delivery | `docker` |
| Conda metadata or packages | `pixi` or `conda` |
| Pure Python packaging | `uv` or `pip` |

Record repository evidence, executable availability, selected version, and any fallback in `backend_choice.json`. A local `package=none` plan must use `uv`, `pip`, `conda`, or `pixi`; `backend=docker` requires a Docker package profile.

## Package policy

- `none`: default when the site permits local Python; successful only with an explicit, validated Python runtime and lock evidence.
- `docker-registry`: successful container profile; requires build, validation, registry push, digest resolution, and digest pull verification.
- `docker-local`: diagnostic; final verdict must not claim Eval readiness.

If Docker delivery was selected and its registry closure is unavailable, stop with a repairable partial/blocked result. Do not convert base Python, an unrecorded environment, or a locally guessed image tag into deployment readiness.

## Device policy

Apply CUDA-first validation rules during local adaptation. A selected image must repeat the device-path checks. CPU fallback evidence may be retained, but the final bundle must accurately describe the validated device and must not claim unsupported GPU readiness.
