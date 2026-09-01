#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path


RESERVED = {
    "model.py",
    "server.py",
    "__init__.py",
    "validate.py",
    "config.yaml",
    "model.spec.yaml",
    "Dockerfile.sure",
    "Dockerfile",
    "artifacts",
    "fixture",
}
REPO_ROOT = Path(__file__).resolve().parents[4]
MODELS_ROOT = REPO_ROOT / "sure" / "models"


def ensure_safe_parent(root: Path, destination: Path) -> None:
    root = root.resolve()
    try:
        relative_parent = destination.parent.relative_to(root)
    except ValueError as error:
        raise ValueError(f"model payload destination escapes bundle: {destination}") from error
    current = root
    for part in relative_parent.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"model payload destination parent must not be a symlink: {current}")
        current.mkdir(exist_ok=True)
        if not current.is_dir():
            raise ValueError(f"model payload destination parent is not a directory: {current}")
    if destination.is_symlink():
        raise ValueError(f"model payload destination must not be a symlink: {destination}")


def copy_file(source: Path, destination: Path, policy: str, source_digest: str, root: Path) -> str:
    ensure_safe_parent(root, destination)
    if destination.exists():
        if destination.is_file() and os.path.samefile(source, destination):
            return "reuse"
        if destination.is_file() and destination.stat().st_size == source.stat().st_size and file_sha256(destination) == source_digest:
            return "reuse"
        raise ValueError(f"model payload destination already exists with different content: {destination}")
    if policy in {"hardlink", "auto"}:
        try:
            os.link(source, destination)
            return "hardlink"
        except OSError:
            if policy == "hardlink":
                raise
    shutil.copy2(source, destination)
    return "copy"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = json.loads((artifacts / "trans_input_resolved.json").read_text(encoding="utf-8"))
    source = Path(resolved["model_path"])
    model_name = str(resolved["model_name"])
    path_policy = resolved.get("path_policy") if isinstance(resolved.get("path_policy"), dict) else {}
    allowed_root = Path(str(path_policy.get("allowed_model_root") or MODELS_ROOT)).expanduser().resolve()
    declared_destination = Path(str(resolved.get("model_dir") or "")).expanduser()
    if declared_destination.is_symlink():
        raise ValueError("model_dir must be a real harness-owned directory, not a symlink")
    destination = declared_destination
    try:
        destination = destination.resolve()
    except OSError:
        destination = destination.absolute()
    if destination.parent != allowed_root or destination.name != model_name:
        raise ValueError(
            f"model_dir must be the harness-owned bundle {allowed_root / model_name}; "
            f"refusing external destination {destination}"
        )
    policy = str(resolved.get("model_stage_policy") or "auto")
    destination.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        raise ValueError(f"model payload source must not be a symlink: {source}")
    if source.is_file():
        files = [source]
    else:
        entries = sorted(source.rglob("*"))
        symlinks = [path for path in entries if path.is_symlink()]
        if symlinks:
            raise ValueError(f"model payload source must not contain symlinks: {symlinks[0]}")
        files = [path for path in entries if path.is_file()]
    if not files:
        raise ValueError(f"model payload contains no files: {source}")
    methods: dict[str, int] = {}
    payload_files: dict[str, dict[str, int | str]] = {}
    total_bytes = 0
    for item in files:
        relative = Path(item.name) if source.is_file() else item.relative_to(source)
        if relative.parts[0] in RESERVED:
            raise ValueError(f"model payload conflicts with reserved SURE bundle path: {relative}")
        digest = file_sha256(item)
        method = copy_file(item, destination / relative, policy, digest, destination)
        methods[method] = methods.get(method, 0) + 1
        total_bytes += item.stat().st_size
        payload_files[relative.as_posix()] = {
            "sha256": digest,
            "size_bytes": item.stat().st_size,
        }
    payload_identity = hashlib.sha256(
        json.dumps(
            {path: entry["sha256"] for path, entry in payload_files.items()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    payload = {
        "schema": "sure.trans.model_payload_manifest.v1",
        "status": "ready",
        "source": str(source),
        "destination": str(destination),
        "policy": policy,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "methods": methods,
        "files": payload_files,
        "payload_identity_sha256": payload_identity,
        "container_mount_target": resolved["model_mount_target"],
    }
    output = artifacts / "model_payload_manifest.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
