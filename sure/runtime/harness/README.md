# Harness Runtime Image

Bootstrap the locked runtime first, then build and publish a content-addressed
runtime image:

```bash
export HARNESS_ROOT=/path/to/sure/.runtime/harness/sure-harness-v1-py311-<lock>
python sure/runtime/harness/build_image.py \
  --runtime-root "$HARNESS_ROOT" \
  --image <registry>/hpc/sure-harness:v1 \
  --push \
  --output sure/runtime/harness/runtime-image.json
```

The output records `image_ref` as `<repository>@sha256:<digest>` together with
the runtime ID and dependency lock hash. Commit that small JSON lock after
reviewing it; never use a mutable tag as the trans build source.

Set `SURE_HARNESS_RUNTIME_IMAGE` to the digest-pinned `image_ref` before running
`scaffold_adapter.py`. The generated adapter Dockerfile still uses
`COPY --from=sure_harness_runtime`; its build command must pass:

```bash
--build-context sure_harness_runtime=docker-image://<repository>@sha256:<digest>
```
