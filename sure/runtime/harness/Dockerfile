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
ARG PYTHON_FULL_VERSION

FROM ${BUILD_IMAGE} AS build
ARG RUNTIME_ID
ARG PYTHON_VERSION
ARG PYTHON_FULL_VERSION
# uv refuses to create a virtual environment in a directory that already exists,
# so its managed interpreter is installed beside the runtime and moved in after
# the environment is built; every absolute link into the old location is then
# re-pointed, which also covers uv's minor-version alias directory.
# The interpreter is pinned to the exact version the host manifest records, and
# the bootstrap below may not download another one, so the runtime in the image
# is the same interpreter build as the runtime the manifest describes.
ENV UV_PYTHON_INSTALL_DIR=/opt/harness-base-python
ENV UV_LINK_MODE=copy
COPY --from=harness_runtime_spec uvenv.py /opt/sure-build/sure/runtime/uvenv.py
COPY --from=harness_runtime_spec harness/bootstrap.py harness/runtime.json harness/requirements.lock.txt /opt/sure-build/sure/runtime/harness/
RUN set -eu; \
    bootstrap=/opt/sure-build/sure/runtime/harness/bootstrap.py; \
    dest="/opt/sure-harness/${RUNTIME_ID}"; \
    case "${PYTHON_FULL_VERSION}" in ${PYTHON_VERSION}.*) ;; \
        *) echo "host interpreter '${PYTHON_FULL_VERSION}' is not a ${PYTHON_VERSION} release" >&2; exit 1;; esac; \
    uv python install "${PYTHON_FULL_VERSION}"; \
    base="$(uv python find "${PYTHON_FULL_VERSION}")"; \
    [ -x "$base" ] || { echo "uv did not install ${PYTHON_FULL_VERSION}" >&2; exit 1; }; \
    UV_PYTHON_DOWNLOADS=never "$base" "$bootstrap" --runtime-root /opt/sure-harness \
        || { cat /opt/sure-harness/logs/*.log >&2; exit 1; }; \
    [ -n "${RUNTIME_ID}" ] && [ -d "$dest" ] \
        || { echo "built runtime is not '${RUNTIME_ID}': $(ls /opt/sure-harness)" >&2; exit 1; }; \
    mv /opt/harness-base-python "$dest/base-python"; \
    sed -i "s|/opt/harness-base-python|$dest/base-python|g" "$dest/pyvenv.cfg"; \
    find "$dest" -type l | while read -r link; do \
        target="$(readlink "$link")"; \
        case "$target" in /opt/harness-base-python/*) \
            ln -sfn "$dest/base-python${target#/opt/harness-base-python}" "$link";; esac; \
    done; \
    "$dest/bin/python" "$bootstrap" --check --runtime-root /opt/sure-harness; \
    built="$("$dest/bin/python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["python_version"])' "$dest/runtime-manifest.json")"; \
    [ "$built" = "${PYTHON_FULL_VERSION}" ] \
        || { echo "image interpreter is $built, host manifest says ${PYTHON_FULL_VERSION}" >&2; exit 1; }

FROM scratch
ARG RUNTIME_ID
COPY --from=build /opt/sure-harness/${RUNTIME_ID}/ /
