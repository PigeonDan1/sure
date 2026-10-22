# Harness Runtime Image

`bootstrap.py` materializes the Harness Runtime as a self-contained tree: uv
installs its managed interpreter beside the environment, and the bootstrap then
moves that interpreter into the tree as `base-python`, re-points every link and
rewrites `pyvenv.cfg` to name the in-tree copy. A uv virtual environment on its
own is not relocatable — `pyvenv.cfg` and `bin/python` both point at a base
interpreter outside the tree — but after the relocation nothing in the tree
reaches outside it, so the same directory can be mounted into a container that
has no host Python and entered through its own `bin/python`.

`pyvenv.cfg home` stays absolute, because CPython reads a relative `home`
against the process working directory and the tree then only starts from one
directory. The consequence is that the tree runs where it was materialized:
mount it at the path it was built at, which is what the container launcher does
(it mounts the repository at its own host path). The image build runs the same
bootstrap at the destination path, so it inherits that recipe.
Because the identity is the lock hash, the image's `runtime_id` equals the
host's by construction, and the build fails if it does not. Bootstrap the host
runtime first; the image is built from its identity:

```bash
export HARNESS_ROOT=/path/to/sure/.runtime/harness/sure-harness-v1-m4-py311-<lock>
python sure/runtime/harness/build_image.py \
  --runtime-root "$HARNESS_ROOT" \
  --image <registry>/sure-harness:v1 \
  --push \
  --output sure/runtime/harness/runtime-image.json
```

`build_image.py` hands `docker build` a named `--build-context`, so this needs
Docker Buildx; the classic builder rejects the flag. The adapter build below
needs it for the same reason.

The host manifest must carry `python_version`, and the image installs exactly
that interpreter and fails when the runtime it built reports a different one, so
the image never claims provenance for a build of Python it does not contain.
With uv's defaults that version is a uv-managed interpreter and installs
cleanly. If the host runtime was materialized on an interpreter uv cannot
install as a managed build, the image build stops at `uv python install`;
materialize the host runtime through uv, which is the default, and build again.

The output records `image_ref` as `<repository>@sha256:<digest>` together with
the runtime ID and dependency lock hash: the identity of the runtime image, not
of the host tree it was built from. Commit that small JSON lock after reviewing
it; never use a mutable tag as the trans build source. Regenerate and recommit
it whenever the dependency lock or `materialization_version` changes, because
`describe_harness_runtime.py` ignores a `runtime-image.json` whose `runtime_id`
and `lock_sha256` no longer match the active runtime, and `scaffold_adapter.py`
refuses outright. This branch changed both, so any `runtime-image.json` produced
before it is stale.

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
its own interpreter; `SURE_HARNESS_BOOTSTRAP_PYTHON` names an interpreter to run
`bootstrap.py` with instead, and `SURE_UV_BIN` names the uv binary. The host
needs no Python of its own, because uv fetches the interpreter and the locked
wheels itself. That first run needs network and can take minutes; later runs
verify the prepared tree and reuse it. The manifest records
`materialization: uv_venv`, `self_contained: true` and the SHA-256 of the base
interpreter uv picked — read back from the in-tree `base-python` on every
verification, so a tree whose interpreter was swapped underneath it is refused
and rebuilt rather than trusted.

`runtime_id` is
`sure-harness-<harness_version>-m<materialization_version>-py311-<lock12>`, so
the materialization version and the dependency lock are both part of the
identity, and this runtime changed both (`materialization_version` 3 → 4, the
bump that makes the self-contained layout part of the identity). The gates
compare those strings against the active runtime: `check_runtime_inventory.py`
rejects an inventory and `check_container_package.py` rejects a container
package whose `runtime_id` or `lock_sha256` differs. A bundle approved under the
previous Harness Runtime is refused until it is approved again over this one.
