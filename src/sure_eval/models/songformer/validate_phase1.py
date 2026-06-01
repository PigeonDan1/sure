from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


MODEL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MODEL_ROOT.parents[3]
ARTIFACTS_DIR = MODEL_ROOT / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "songformer" / "song_24k.wav"
LOCAL_DIR = MODEL_ROOT / "ckpts" / "SongFormer"
HF_HOME = MODEL_ROOT / "hf_cache"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(stage: str, status: str, message: str, extra: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "timestamp": now_iso(),
        "stage": stage,
        "status": status,
        "message": message,
    }
    if extra:
        payload.update(extra)
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def main() -> None:
    started = time.time()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(HF_HOME))
    os.environ.setdefault("HF_HUB_CACHE", str(HF_HOME / "hub"))

    if not FIXTURE.exists():
        raise FileNotFoundError(f"Fixture missing: {FIXTURE}")
    if not LOCAL_DIR.exists():
        raise FileNotFoundError(f"Model snapshot missing: {LOCAL_DIR}")

    result: dict[str, Any] = {
        "timestamp": now_iso(),
        "model_id": "ASLP-lab/SongFormer",
        "fixture_path": str(FIXTURE),
        "snapshot_dir": str(LOCAL_DIR),
        "tests": {},
    }

    import_started = time.time()
    from huggingface_hub import snapshot_download
    from transformers import AutoModel

    result["tests"]["import"] = {
        "passed": True,
        "duration_ms": round((time.time() - import_started) * 1000, 3),
    }
    append_log("VALIDATE_IMPORT", "passed", "Import test passed.")

    load_started = time.time()
    local_dir = snapshot_download(
        repo_id="ASLP-lab/SongFormer",
        repo_type="model",
        local_dir=str(LOCAL_DIR),
        local_dir_use_symlinks=False,
        resume_download=True,
        allow_patterns="*",
        ignore_patterns=["SongFormer.pt", "SongFormer.safetensors"],
    )
    sys.path.append(local_dir)
    os.environ["SONGFORMER_LOCAL_DIR"] = local_dir
    model = AutoModel.from_pretrained(
        local_dir,
        trust_remote_code=True,
        low_cpu_mem_usage=False,
    )
    device = "cuda:0" if __import__("torch").cuda.is_available() else "cpu"
    model = model.to(device)
    model.eval()
    result["tests"]["load"] = {
        "passed": True,
        "duration_ms": round((time.time() - load_started) * 1000, 3),
        "device": device,
    }
    append_log(
        "VALIDATE_LOAD",
        "passed",
        "Load test passed.",
        {"snapshot_dir": local_dir, "device": device},
    )

    infer_started = time.time()
    raw_result = model(str(FIXTURE))
    normalized = to_jsonable(raw_result)
    result["tests"]["infer"] = {
        "passed": True,
        "duration_ms": round((time.time() - infer_started) * 1000, 3),
        "device": device,
    }
    append_log(
        "VALIDATE_INFER",
        "passed",
        "Infer test passed.",
        {"fixture_source": "model_specific_fallback", "fixture_used": str(FIXTURE), "device": device},
    )

    contract_started = time.time()
    if not isinstance(normalized, list) or not normalized:
        raise AssertionError("Expected a non-empty list of segments")
    for index, item in enumerate(normalized):
        if not isinstance(item, dict):
            raise AssertionError(f"Segment {index} is not a dict")
        for key in ("start", "end", "label"):
            if key not in item:
                raise AssertionError(f"Segment {index} missing required field: {key}")
        if not str(item["label"]).strip():
            raise AssertionError(f"Segment {index} has empty label")
    json.dumps(normalized, ensure_ascii=False)
    SAMPLE_OUTPUT.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["tests"]["contract"] = {
        "passed": True,
        "duration_ms": round((time.time() - contract_started) * 1000, 3),
        "segment_count": len(normalized),
    }
    append_log(
        "VALIDATE_CONTRACT",
        "passed",
        "Contract test passed.",
        {
            "primary_field": "segments",
            "required_fields": ["start", "end", "label"],
            "segment_count": len(normalized),
        },
    )

    result["result"] = normalized
    result["overall"] = "PASSED"
    result["duration_seconds"] = round(time.time() - started, 3)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
