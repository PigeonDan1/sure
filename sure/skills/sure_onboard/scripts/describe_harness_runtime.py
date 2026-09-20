#!/usr/bin/env python3
"""Describe the approved common Harness Runtime for a Docker build context."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"HARNESS_RUNTIME_NOT_READY: {name} is not set")
    return value


def describe() -> dict[str, Any]:
    runtime_id = _required("SURE_HARNESS_RUNTIME_ID")
    lock_sha256 = _required("SURE_HARNESS_LOCK_SHA256")
    manifest_path = Path(_required("SURE_HARNESS_MANIFEST_PATH")).resolve()
    runtime_root = Path(_required("SURE_HARNESS_RUNTIME_ROOT")).resolve()
    # Resolve the directory only: in a venv bin/python is a symlink to the base
    # interpreter, and following it would always land outside the runtime root.
    python = Path(_required("HARNESS_PYTHON_BIN"))
    python = python.parent.resolve() / python.name
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "sure.harness.runtime.manifest.v1":
        raise ValueError("HARNESS_RUNTIME_NOT_READY: unsupported runtime manifest")
    if manifest.get("runtime_id") != runtime_id or manifest.get("lock_sha256") != lock_sha256:
        raise ValueError("HARNESS_RUNTIME_NOT_READY: environment and manifest identity disagree")
    if manifest_path.parent != runtime_root or not python.is_relative_to(runtime_root):
        raise ValueError("HARNESS_RUNTIME_NOT_READY: runtime paths are inconsistent")
    destination = f"/opt/sure-harness/{runtime_id}"
    image_ref = os.environ.get("SURE_HARNESS_RUNTIME_IMAGE", "").strip()
    if not image_ref:
        config_path = Path(__file__).resolve().parents[3] / "runtime" / "harness" / "runtime-image.json"
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if config.get("runtime_id") == runtime_id and config.get("lock_sha256") == lock_sha256:
                image_ref = str(config.get("image_ref") or "")
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image_ref):
        # The runtime is a uv virtual environment, which is not relocatable: copying
        # the host tree into an image yields a dead interpreter. The image builds its
        # own from the same lock, so a digest-pinned runtime image is the only source.
        raise ValueError(
            "HARNESS_RUNTIME_NOT_READY: no digest-pinned Harness Runtime image for "
            f"{runtime_id}; build one with sure/runtime/harness/build_image.py and set "
            "SURE_HARNESS_RUNTIME_IMAGE"
        )
    return {
        "schema": "sure.harness.runtime.container_build.v1",
        "runtime_id": runtime_id,
        "lock_sha256": lock_sha256,
        "python_version": manifest.get("python_version"),
        "python_abi": manifest.get("python_abi"),
        "required_imports": manifest.get("required_imports") or [],
        "build_context": {
            "name": "sure_harness_runtime",
            "source": f"docker-image://{image_ref}",
            "docker_build_option": f"--build-context sure_harness_runtime=docker-image://{image_ref}",
            "dockerfile_copy": f"COPY --from=sure_harness_runtime / {destination}/",
        },
        "image_binding": {
            "schema": "sure.harness.runtime.binding.v1",
            "runtime_id": runtime_id,
            "runtime_type": "harness_python",
            "python_executable": f"{destination}/bin/python",
            "python_version": manifest.get("python_version"),
            "python_abi": manifest.get("python_abi"),
            "lock_sha256": lock_sha256,
            "manifest_path": f"{destination}/runtime-manifest.json",
            "runtime_root": destination,
            "materialization": "image_copy",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = describe()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"describe_harness_runtime failed: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
