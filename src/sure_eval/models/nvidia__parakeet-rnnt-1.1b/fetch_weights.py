#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_ID = "nvidia/parakeet-rnnt-1.1b"
MODELSCOPE_CANDIDATES = [
    "nv-community/parakeet-rnnt-1.1b",
    "nvidia/parakeet-rnnt-1.1b",
]
MODEL_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = MODEL_DIR / "artifacts"
RUNTIME_DIR = MODEL_DIR / ".runtime"
CHECKPOINTS_DIR = MODEL_DIR / "checkpoints"
HF_HOME = RUNTIME_DIR / "hf-home"
HF_CACHE = HF_HOME / "hub"
NEMO_CACHE = RUNTIME_DIR / "nemo-cache"
NEMO_FILENAME = "parakeet-rnnt-1.1b.nemo"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure_cache() -> None:
    for path in [HF_HOME, HF_CACHE, NEMO_CACHE, RUNTIME_DIR / "tmp", CHECKPOINTS_DIR]:
        path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(HF_HOME))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE))
    os.environ.setdefault("NEMO_CACHE_DIR", str(NEMO_CACHE))
    os.environ.setdefault("TMPDIR", str(RUNTIME_DIR / "tmp"))


def write_manifest(payload: dict[str, Any]) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS_DIR / "weights_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def find_nemo_checkpoint(snapshot_path: str | os.PathLike[str]) -> Path | None:
    root = Path(snapshot_path)
    if root.is_file() and root.suffix == ".nemo":
        return root
    candidates = sorted(root.rglob("*.nemo")) if root.exists() else []
    if not candidates:
        return None
    preferred = [path for path in candidates if path.name == NEMO_FILENAME]
    return preferred[0] if preferred else candidates[0]


def materialize_checkpoint(nemo_source: Path | None) -> tuple[Path | None, bool, str | None]:
    if nemo_source is None:
        return None, False, "no .nemo checkpoint found in downloaded snapshot"

    destination = CHECKPOINTS_DIR / nemo_source.name
    if destination.exists() and destination.stat().st_size == nemo_source.stat().st_size:
        return destination, True, "existing checkpoint reused"
    if destination.exists():
        destination.unlink()

    try:
        os.link(nemo_source, destination)
        method = "hardlink"
    except OSError:
        shutil.copy2(nemo_source, destination)
        method = "copy"
    return destination, True, method


def discover_existing_snapshot() -> tuple[str, str, str | None, str] | None:
    modelscope_cache = RUNTIME_DIR / "modelscope_cache"
    for candidate in MODELSCOPE_CANDIDATES:
        snapshot = modelscope_cache / candidate
        if find_nemo_checkpoint(snapshot):
            return (
                str(snapshot.resolve()),
                "modelscope_mirror",
                candidate,
                str(modelscope_cache.resolve()),
            )

    checkpoint = CHECKPOINTS_DIR / NEMO_FILENAME
    if checkpoint.exists():
        return (
            str(checkpoint.resolve()),
            "model_local_checkpoint",
            None,
            str(CHECKPOINTS_DIR.resolve()),
        )
    return None


def main() -> None:
    configure_cache()
    existing = discover_existing_snapshot()
    if existing is not None:
        snapshot_path, source, mirror_model_id, provider_cache_path = existing
    else:
        from huggingface_hub import snapshot_download

        source = "huggingface"
        provider_cache_path = str(HF_CACHE.resolve())
        mirror_model_id = None
        try:
            snapshot_path = snapshot_download(
                repo_id=MODEL_ID,
                cache_dir=str(HF_CACHE),
                local_files_only=os.environ.get("HF_LOCAL_FILES_ONLY") == "1",
            )
        except Exception as hf_exc:
            last_exc: Exception | None = hf_exc
            try:
                from modelscope import snapshot_download as modelscope_snapshot_download
            except Exception:
                raise hf_exc
            modelscope_cache = RUNTIME_DIR / "modelscope_cache"
            modelscope_cache.mkdir(parents=True, exist_ok=True)
            for candidate in MODELSCOPE_CANDIDATES:
                try:
                    snapshot_path = modelscope_snapshot_download(
                        candidate,
                        cache_dir=str(modelscope_cache),
                    )
                    source = "modelscope_mirror"
                    provider_cache_path = str(modelscope_cache.resolve())
                    mirror_model_id = candidate
                    break
                except Exception as ms_exc:
                    last_exc = ms_exc
            else:
                raise RuntimeError(
                    f"Unable to fetch {MODEL_ID} from Hugging Face or ModelScope candidates "
                    f"{MODELSCOPE_CANDIDATES}. Last error: {last_exc}"
                ) from last_exc
    nemo_source = find_nemo_checkpoint(snapshot_path)
    checkpoint_path, checkpoint_materialized, checkpoint_method = materialize_checkpoint(
        nemo_source
    )
    runtime_load_identity = (
        str(checkpoint_path.resolve()) if checkpoint_path is not None else MODEL_ID
    )
    payload: dict[str, Any] = {
        "timestamp": now_iso(),
        "model_id": MODEL_ID,
        "weights_repo_id": MODEL_ID,
        "source": source,
        "mirror_model_id": mirror_model_id,
        "cache_policy": "model_local_first",
        "materialization_strategy": f"{source}_runtime_cache",
        "checkpoint_materialized": checkpoint_materialized,
        "checkpoint_materialization_method": checkpoint_method,
        "runtime_load_identity": runtime_load_identity,
        "resolved_local_model_path": runtime_load_identity,
        "nemo_checkpoint_source": str(nemo_source.resolve()) if nemo_source else None,
        "snapshot_path": snapshot_path,
        "runtime_root": str(RUNTIME_DIR.resolve()),
        "checkpoints_dir": str(CHECKPOINTS_DIR.resolve()),
        "hf_home": str(HF_HOME.resolve()),
        "hub_cache_path": str(HF_CACHE.resolve()),
        "nemo_cache_dir": str(NEMO_CACHE.resolve()),
        "provider_cache_path": provider_cache_path,
        "host_fallback_used": False,
        "notes": "Runtime loads the model-local .nemo archive with EncDecRNNTBPEModel.restore_from; no host cache fallback is used.",
    }
    if checkpoint_path and checkpoint_path.exists():
        payload["files"] = [
            {
                "filename": checkpoint_path.name,
                "path": str(checkpoint_path.resolve()),
                "size_bytes": checkpoint_path.stat().st_size,
            }
        ]
    write_manifest(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
