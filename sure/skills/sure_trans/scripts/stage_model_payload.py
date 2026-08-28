#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path


RESERVED = {"model.py", "server.py", "config.yaml", "model.spec.yaml", "artifacts"}
REPO_ROOT = Path(__file__).resolve().parents[4]
MODELS_ROOT = REPO_ROOT / "sure" / "models"


def copy_file(source: Path, destination: Path, policy: str) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_file() and destination.stat().st_size == source.stat().st_size and file_sha256(destination) == file_sha256(source):
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
    destination = Path(str(resolved.get("model_dir") or "")).expanduser()
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
    files = [source] if source.is_file() else sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"model payload contains no files: {source}")
    methods: dict[str, int] = {}
    total_bytes = 0
    for item in files:
        relative = Path(item.name) if source.is_file() else item.relative_to(source)
        if relative.parts[0] in RESERVED:
            raise ValueError(f"model payload conflicts with reserved SURE bundle path: {relative}")
        method = copy_file(item, destination / relative, policy)
        methods[method] = methods.get(method, 0) + 1
        total_bytes += item.stat().st_size
    payload = {
        "schema": "sure.trans.model_payload_manifest.v1",
        "status": "ready",
        "source": str(source),
        "destination": str(destination),
        "policy": policy,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "methods": methods,
        "container_mount_target": resolved["model_mount_target"],
    }
    output = artifacts / "model_payload_manifest.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
