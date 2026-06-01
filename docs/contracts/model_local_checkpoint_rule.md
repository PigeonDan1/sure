# Model-Local Checkpoint Cache Rule

## Purpose

This rule defines where model weights and runtime caches should live during tool
onboarding.

Its goal is to prevent a model from passing onboarding on one machine while its
configured runtime later fails on another machine because checkpoints were
stored in a host-global cache that is not reflected in model-local artifacts.

## Default Rule

For local models, checkpoint-related assets should default to paths under the
model directory:

```text
src/sure_eval/models/<model>/
├── checkpoints/
│   └── ...
└── .runtime/
    ├── .venv/
    ├── uv-cache/
    └── hf-home/
```

Recommended interpretation:

- `checkpoints/` stores the resolved local model path that runtime code should
  be able to load directly
- `.runtime/.venv/` stores the model-local interpreter when a local venv is
  used
- `.runtime/hf-home/` stores Hugging Face cache state when Hugging Face is the
  source
- `.runtime/uv-cache/` or equivalent stores build-time package cache

## Required Behavior

The tool onboarding workflow should prefer:

1. model-local checkpoint storage
2. model-local runtime/cache storage
3. explicit artifact records that point to those paths

If the runtime ultimately loads a local checkpoint, the configured runtime path
should resolve to a path under the model directory whenever feasible.

## Required Artifact Recording

When `weights.required == true`, the workflow should record in
`artifacts/weights_manifest.json`:

- the resolved local model path
- the checkpoint/cache root
- the source used to fetch weights
- any host-specific fallback path, if one was required

When a build plan is generated, `artifacts/build_plan.json` should record:

- `build_root`
- `venv_path`
- `cache_path`
- `hf_cache_path` when applicable
- whether these paths are model-local or host-fallback paths

## Fallback Rule

Host-global or external cache locations are allowed only when there is an
explicit constraint such as:

- workspace capacity is insufficient
- filesystem permissions prevent model-local runtime creation
- the upstream runtime hardcodes a global cache path

When fallback is used, both `build_plan.json` and `weights_manifest.json` must
record:

- why model-local storage was not used
- which fallback path was chosen
- how runtime code should still locate the checkpoint on this host

## Must Not Do

- must not rely only on an implicit Hugging Face cache outside the model
  directory without recording it
- must not leave `MODEL_PATH` as a remote identifier when a local checkpoint
  was already fetched and should be preferred
- must not mark a model as ready if its local runtime cannot deterministically
  rediscover the downloaded checkpoint
