# Backend Selection Policy

**Version**: 2.0
**Scope**: `/sure_onboard` local adaptation and container delivery

## Two separate decisions

`backend_choice.json.backend` selects the environment used to adapt and validate the model locally. It may be `uv`, `pip`, `conda`, `pixi`, or `docker`. `package_profile` selects the delivered Eval runtime.

Docker registry delivery remains the default. For that profile, the decisions converge as follows:

1. Adapt with the repository-compatible backend.
2. Run import, load, inference, and contract checks in that environment.
3. Adapt a Dockerfile that reproduces the passing environment.
4. Build and repeat the bounded checks inside the image.
5. Push the image and pull-verify its immutable registry digest.
6. Publish `runtime_inventory.json` with `execution_mode=container_only`.

The local backend is evidence used to create the image. It is not an implicit Eval fallback. An explicit `package=none` follows the separate sealed Python policy below.

## Local backend routing

| Evidence | Adaptation backend |
|---|---|
| API-only model | `api`; package profile must be `none` |
| Upstream Dockerfile or complex OS/CUDA dependencies | `docker` |
| Conda metadata or packages | `pixi` or `conda` |
| Pure Python packaging | `uv` or `pip` |

Record repository evidence, executable availability, selected version, and any fallback in `backend_choice.json`. A fallback may change the adaptation backend, but may not silently change the selected package profile.

## Package policy

- `docker-registry`: default successful local-model profile.
- `docker-local`: diagnostic; final verdict must not claim Eval readiness.
- `none`: API delivery, or an explicit local Python profile when the site permits Python, backend=`uv`, and a hash-locked Model Runtime has been materialized and sealed. It never enables VC execution.

If the selected profile's required delivery checks are unavailable, stop with a repairable partial/blocked result. Do not convert a model-local `.venv`, arbitrary base Python, or a locally guessed image tag into deployment readiness.

## Device policy

Apply CUDA-first validation rules during local adaptation. The image must then repeat the selected device-path checks. CPU fallback evidence may be retained, but the final bundle must accurately describe the validated device and must not claim unsupported GPU readiness.
