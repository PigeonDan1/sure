#!/usr/bin/env python3
"""Build and optionally publish a digest-pinned Harness Runtime image."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
SPEC_PATH = Path(__file__).resolve().parent / "runtime.json"


def repository_of(reference: str) -> str:
    """Drop the tag from a docker reference without mistaking a registry port for one.

    A port is only a tag separator when nothing after it looks like a path, so
    registry.example:5000/hpc/sure-harness keeps its repository intact.
    """
    base = reference.split("@", 1)[0]
    head, separator, tail = base.rpartition(":")
    if separator and "/" not in tail:
        return head
    return base


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--image", required=True, help="mutable build tag, e.g. registry/hpc/sure-harness:v1")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    runtime_root = args.runtime_root.expanduser().resolve()
    if not (runtime_root / "bin" / "python").is_file():
        raise ValueError(f"runtime root is missing bin/python: {runtime_root}")
    if not (runtime_root / "runtime-manifest.json").is_file():
        raise ValueError(f"runtime root is missing runtime-manifest.json: {runtime_root}")
    manifest = read_json(runtime_root / "runtime-manifest.json")
    spec = read_json(SPEC_PATH)
    for key in ("runtime_id", "lock_sha256"):
        if not manifest.get(key):
            raise ValueError(f"runtime manifest is missing {key}")
    if manifest.get("runtime_id", "").startswith("sure-harness-") is False:
        raise ValueError("runtime manifest has an invalid runtime_id")
    lock_sha256 = sha256_file(SPEC_PATH.parent / str(spec.get("lock_file") or ""))
    if manifest.get("lock_sha256") != lock_sha256:
        raise ValueError("runtime manifest lock_sha256 does not match the runtime spec")
    dockerfile = Path(__file__).resolve().parent / "Dockerfile"
    command = [
        "docker", "build", "--progress", "plain",
        "--build-context", f"harness_runtime_source={runtime_root}",
        "--label", f"org.sure.harness.runtime_id={manifest['runtime_id']}",
        "--label", f"org.sure.harness.lock_sha256={manifest['lock_sha256']}",
        "--file", str(dockerfile), "--tag", args.image, str(dockerfile.parent),
    ]
    built = run(command)
    if built.returncode != 0:
        raise RuntimeError((built.stderr or built.stdout).strip() or f"docker build exited {built.returncode}")
    digest = ""
    if args.push:
        pushed = run(["docker", "push", args.image])
        if pushed.returncode != 0:
            raise RuntimeError((pushed.stderr or pushed.stdout).strip() or f"docker push exited {pushed.returncode}")
        match = re.search(r"digest:\s*(sha256:[0-9a-f]{64})", pushed.stdout + pushed.stderr, re.IGNORECASE)
        digest = match.group(1) if match else ""
    inspect = run(["docker", "image", "inspect", args.image, "--format", "{{json .}}"])
    if inspect.returncode != 0:
        raise RuntimeError((inspect.stderr or inspect.stdout).strip() or "runtime image inspect failed")
    inspected = json.loads(inspect.stdout)
    repo_digests = inspected.get("RepoDigests") if isinstance(inspected, dict) else []
    repository = repository_of(args.image)
    if not digest and isinstance(repo_digests, list):
        for value in repo_digests:
            if isinstance(value, str) and value.startswith(f"{repository}@sha256:"):
                digest = value.split("@", 1)[1]
                break
    payload = {
        "schema": "sure.harness.runtime.image.v1",
        "image": args.image,
        "image_ref": f"{repository}@{digest}" if digest else None,
        "runtime_id": manifest["runtime_id"],
        "lock_sha256": manifest["lock_sha256"],
        "push_requested": args.push,
        "digest_pinned": bool(digest),
    }
    if args.output:
        args.output.expanduser().resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.push and not DIGEST_RE.fullmatch(str(payload["image_ref"] or "")):
        raise RuntimeError("pushed runtime image did not produce a digest-pinned reference")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"build_image failed: {error}")
        raise SystemExit(1)
