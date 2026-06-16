#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xforge_sure_bridge.bridge import (
    BridgeError,
    load_manifest,
    materialize_model_manifest,
    write_summary,
)


def collect_remote_model_source(manifest: dict[str, Any], model_dir: Path) -> dict[str, Any]:
    source = manifest.get("source", {})
    if not isinstance(source, dict):
        raise BridgeError("model source must be a JSON object")
    provider = source.get("provider")
    source_id = source.get("id")
    if not provider or not source_id:
        raise BridgeError("model source requires provider and id")

    runtime_root = model_dir / ".runtime"
    if provider == "local":
        return manifest

    if provider == "huggingface":
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise BridgeError("huggingface_hub is required for provider='huggingface'") from exc
        huggingface_cache = runtime_root / "huggingface_cache"
        huggingface_cache.mkdir(parents=True, exist_ok=True)
        local_path = snapshot_download(
            repo_id=str(source_id),
            local_dir=str(huggingface_cache / str(source_id).replace("/", "__")),
            local_dir_use_symlinks=False,
        )
    elif provider == "modelscope":
        try:
            from modelscope import snapshot_download
        except ImportError as exc:
            raise BridgeError("modelscope is required for provider='modelscope'") from exc
        modelscope_cache = runtime_root / "modelscope_cache"
        modelscope_cache.mkdir(parents=True, exist_ok=True)
        local_path = snapshot_download(
            model_id=str(source_id),
            cache_dir=str(modelscope_cache),
        )
    else:
        raise BridgeError(f"unsupported model provider: {provider}")

    collected = dict(manifest)
    collected["source"] = {"provider": "local", "id": str(local_path), "original_source": source}
    return collected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect a speech model and materialize SURE model-local weights"
    )
    parser.add_argument("--manifest", required=True, help="XForge model manifest JSON")
    parser.add_argument("--model-dir", required=True, help="SURE model directory")
    parser.add_argument("--summary", help="Optional collection summary JSON")
    args = parser.parse_args()

    model_dir = Path(args.model_dir)
    try:
        manifest = collect_remote_model_source(load_manifest(args.manifest), model_dir)
        summary = materialize_model_manifest(manifest, model_dir)
        if args.summary:
            write_summary(args.summary, summary)
    except BridgeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
