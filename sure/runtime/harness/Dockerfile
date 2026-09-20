# The Harness Runtime is a uv virtual environment, and a uv virtual environment
# is not relocatable: pyvenv.cfg and bin/python point at a base interpreter that
# lives outside the tree, so copying a host runtime into an image ships a Python
# that cannot start. This image runs the same bootstrap against the same locked
# requirements, at the destination path, and moves uv's managed interpreter into
# the tree so the tree is self-contained again. The identity is the dependency
# lock, so the runtime_id built here equals the host's by construction; the
# build stops if it does not.
#
# Pinned by digest, and from ghcr so an anonymous pull needs no registry
# credentials: it carries uv and a glibc userland, which the managed CPython
# needs both here and in the adapter images this tree is copied into.
ARG BUILD_IMAGE=ghcr.io/astral-sh/uv:0.12.15-trixie-slim@sha256:01db3cd653df8521c36836516a381f653d09a35631876ce367fc076ba02da4b4
ARG RUNTIME_ID
ARG PYTHON_VERSION=3.11

FROM ${BUILD_IMAGE} AS build
ARG RUNTIME_ID
ARG PYTHON_VERSION
# uv refuses to create a virtual environment in a directory that already exists,
# so its managed interpreter is installed beside the runtime and moved in after
# the environment is built.
ENV UV_PYTHON_INSTALL_DIR=/opt/harness-base-python
ENV UV_LINK_MODE=copy
COPY --from=harness_runtime_spec uvenv.py /opt/sure-build/sure/runtime/uvenv.py
COPY --from=harness_runtime_spec harness/bootstrap.py harness/runtime.json harness/requirements.lock.txt /opt/sure-build/sure/runtime/harness/
RUN set -eu; \
    bootstrap=/opt/sure-build/sure/runtime/harness/bootstrap.py; \
    dest="/opt/sure-harness/${RUNTIME_ID}"; \
    uv python install "${PYTHON_VERSION}"; \
    base="$(ls -d /opt/harness-base-python/cpython-*/bin/python${PYTHON_VERSION})"; \
    "$base" "$bootstrap" --runtime-root /opt/sure-harness \
        || { cat /opt/sure-harness/logs/*.log >&2; exit 1; }; \
    [ -n "${RUNTIME_ID}" ] && [ -d "$dest" ] \
        || { echo "built runtime is not '${RUNTIME_ID}': $(ls /opt/sure-harness)" >&2; exit 1; }; \
    mv /opt/harness-base-python "$dest/base-python"; \
    sed -i "s|/opt/harness-base-python|$dest/base-python|g" "$dest/pyvenv.cfg"; \
    interpreter="$dest/base-python${base#/opt/harness-base-python}"; \
    for name in python python3 "python${PYTHON_VERSION}"; do \
        ln -sfn "$interpreter" "$dest/bin/$name"; \
    done; \
    "$dest/bin/python" "$bootstrap" --check --runtime-root /opt/sure-harness

FROM scratch
ARG RUNTIME_ID
COPY --from=build /opt/sure-harness/${RUNTIME_ID}/ /
