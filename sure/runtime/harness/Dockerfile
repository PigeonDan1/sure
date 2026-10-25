# The Harness Runtime is a self-contained tree: bootstrap.py installs uv's
# managed interpreter beside the environment and then moves it in as
# `base-python`, re-points every link and rewrites pyvenv.cfg to name the
# in-tree copy. The image build below runs that same bootstrap, so it inherits
# the relocation for free and the manual move/sed/symlink loop that older
# revisions needed here is gone. The identity is the dependency lock, so the
# runtime_id built here equals the host's by construction; the build stops if it
# does not.
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
# bootstrap.py is the single source of the relocation recipe: it asks uv for a
# managed interpreter under UV_PYTHON_INSTALL_DIR, builds the venv with copy
# links, moves that interpreter into the tree, and refuses a tree that still
# reaches outside itself. UV_LINK_MODE=copy keeps every wheel inside the tree
# so the scratch layer below is a plain copy with nothing dangling. bootstrap's
# own UV_PYTHON_INSTALL_DIR override hides the interpreter pre-installed here,
# so SURE_HARNESS_PYTHON_VERSION pins the venv request to the exact interpreter
# the host manifest records; without it uv would fetch the newest patch release
# at build time and the identity check at the end would fail on timing alone.
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
    SURE_HARNESS_PYTHON_VERSION="${PYTHON_FULL_VERSION}" "$base" "$bootstrap" --runtime-root /opt/sure-harness \
        || { cat /opt/sure-harness/logs/*.log >&2; exit 1; }; \
    [ -n "${RUNTIME_ID}" ] && [ -d "$dest" ] \
        || { echo "built runtime is not '${RUNTIME_ID}': $(ls /opt/sure-harness)" >&2; exit 1; }; \
    "$dest/bin/python" "$bootstrap" --check --runtime-root /opt/sure-harness; \
    built="$("$dest/bin/python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["python_version"])' "$dest/runtime-manifest.json")"; \
    [ "$built" = "${PYTHON_FULL_VERSION}" ] \
        || { echo "image interpreter is $built, host manifest says ${PYTHON_FULL_VERSION}" >&2; exit 1; }

FROM scratch
ARG RUNTIME_ID
COPY --from=build /opt/sure-harness/${RUNTIME_ID}/ /
