#!/usr/bin/env python3
"""Download and verify MOSS-Transcribe-preview-2B into model-local HF cache."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from huggingface_hub import snapshot_download


MODEL_ROOT = Path(__file__).resolve().parent
ARTIFACTS_DIR = MODEL_ROOT / "artifacts"
REPO = "OpenMOSS-Team/MOSS-Transcribe-preview-2B"
REVISION = "c98175cb20e48bd9be4e95f6c85f2af18899f780"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def configure_env() -> None:
    runtime = MODEL_ROOT / ".runtime"
    defaults = {
        "HF_HOME": runtime / "hf-home",
        "HF_HUB_CACHE": runtime / "hf-home" / "hub",
        "TRANSFORMERS_CACHE": runtime / "hf-home" / "hub",
        "TMPDIR": runtime / "tmp",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, str(value))
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)


def dir_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> int:
    configure_env()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    local_path = Path(
        snapshot_download(
            repo_id=REPO,
            revision=REVISION,
            cache_dir=os.environ["HF_HUB_CACHE"],
            local_files_only=os.environ.get("HF_HUB_OFFLINE", "").lower() in {"1", "true", "yes"},
        )
    )
    expected = [
        "config.json",
        "model.safetensors.index.json",
        "model-00000-of-00001.safetensors",
        "modeling_Moss.py",
        "processing_Moss.py",
        "chat_template_default.py",
        "tokenizer.json",
        "tokenizer_config.json",
    ]
    files_verified = {name: (local_path / name).exists() for name in expected}
    missing = [name for name, exists in files_verified.items() if not exists]
    manifest: dict[str, Any] = {
        "timestamp": now_iso(),
        "model": "OpenMOSS-Team__MOSS-Transcribe-preview-2B",
        "weights_required": True,
        "source": "huggingface",
        "repo_id": REPO,
        "revision": REVISION,
        "resolved_local_model_path": str(local_path),
        "checkpoint_dir": "checkpoints",
        "cache_root": os.environ["HF_HUB_CACHE"],
        "download_status": "completed" if not missing else "incomplete",
        "duration_seconds": round(time.time() - started, 3),
        "size_bytes_observed": dir_size(local_path),
        "files_verified": files_verified,
        "missing": missing,
        "fallback": {
            "used": False
        }
    }
    (ARTIFACTS_DIR / "weights_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
