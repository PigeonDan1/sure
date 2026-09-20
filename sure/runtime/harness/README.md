# Harness Runtime Image

The Harness Runtime is a uv virtual environment, and a uv virtual environment is
not relocatable: `pyvenv.cfg` and `bin/python` both point at a base interpreter
that lives outside the tree. Copying the host runtime into a container yields a
Python that cannot start. So the image builds its own runtime from the same
dependency lock, at the same destination path, with the managed interpreter
inside that path. Because the identity is the lock hash, the image's
`runtime_id` equals the host's by construction, and the build fails if it does
not. Bootstrap the host runtime first; the image is built from its identity:

```bash
export HARNESS_ROOT=/path/to/sure/.runtime/harness/sure-harness-v1-m3-py311-<lock>
python sure/runtime/harness/build_image.py \
  --runtime-root "$HARNESS_ROOT" \
  --image <registry>/sure-harness:v1 \
  --push \
  --output sure/runtime/harness/runtime-image.json
```

The output records `image_ref` as `<repository>@sha256:<digest>` together with
the runtime ID and dependency lock hash. Commit that small JSON lock after
reviewing it; never use a mutable tag as the trans build source.

Set `SURE_HARNESS_RUNTIME_IMAGE` to the digest-pinned `image_ref` before running
`describe_harness_runtime.py` or `scaffold_adapter.py`. Without a digest-pinned
runtime image both refuse to continue, because there is no other way to put a
working Harness Runtime into a model image. The generated adapter Dockerfile
still uses `COPY --from=sure_harness_runtime`; its build command must pass:

```bash
--build-context sure_harness_runtime=docker-image://<repository>@sha256:<digest>
```

## Runtime identity

`bootstrap.py` materializes the host runtime, and the skills start it through
`uv run --no-project --python 3.11`. `SURE_HARNESS_BOOTSTRAP_PYTHON` names an
interpreter to run `bootstrap.py` with instead; `SURE_UV_BIN` names the uv
binary. The host needs no Python of its own, because uv fetches the interpreter
and the locked wheels itself. That first run needs network and can take
minutes; later runs verify the prepared tree and reuse it. The manifest records
`materialization: uv_venv` and the SHA-256 of the base interpreter uv picked.

`runtime_id` is
`sure-harness-<harness_version>-m<materialization_version>-py311-<lock12>`, so
the materialization version and the dependency lock are both part of the
identity, and this runtime changed both. The gates compare those strings
against the active runtime: `check_runtime_inventory.py` rejects an inventory
and `check_container_package.py` rejects a container package whose `runtime_id`
or `lock_sha256` differs. A bundle approved under the previous Harness Runtime
is refused until it is approved again over this one.
