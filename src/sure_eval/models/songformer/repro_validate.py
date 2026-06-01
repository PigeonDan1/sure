from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    fixture = Path(os.environ["SURE_REPRO_FIXTURE"]).resolve()
    weights_dir = Path(os.environ["SURE_REPRO_WEIGHTS_DIR"]).resolve()
    output_path = Path(os.environ["SURE_REPRO_OUTPUT"]).resolve()
    hf_home = Path(os.environ["SURE_REPRO_HF_HOME"]).resolve()

    if not fixture.exists():
        raise FileNotFoundError(f"Fixture missing: {fixture}")
    if not weights_dir.exists():
        raise FileNotFoundError(f"Snapshot directory missing: {weights_dir}")

    os.environ["HF_HOME"] = str(hf_home)
    os.environ["HF_HUB_CACHE"] = str(hf_home / "hub")
    os.environ["SONGFORMER_LOCAL_DIR"] = str(weights_dir)
    weights_dir_str = str(weights_dir)
    if weights_dir_str in sys.path:
        sys.path.remove(weights_dir_str)
    sys.path.insert(0, weights_dir_str)
    existing_model_module = sys.modules.get("model")
    if existing_model_module is not None and getattr(existing_model_module, "__file__", "").endswith("/support/model.py"):
        sys.modules.pop("model", None)

    from transformers import AutoModel

    started = time.time()

    import_started = time.time()
    model = AutoModel.from_pretrained(
        str(weights_dir),
        trust_remote_code=True,
        low_cpu_mem_usage=False,
    )
    device = "cuda:0" if __import__("torch").cuda.is_available() else "cpu"
    model = model.to(device)
    model.eval()
    import_and_load_duration_ms = round((time.time() - import_started) * 1000, 3)

    infer_started = time.time()
    raw_result = model(str(fixture))
    normalized = to_jsonable(raw_result)
    infer_duration_ms = round((time.time() - infer_started) * 1000, 3)

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

    payload = {
        "timestamp": now_iso(),
        "fixture_path": str(fixture),
        "weights_dir": str(weights_dir),
        "hf_home": str(hf_home),
        "tests": {
            "import": {"passed": True, "duration_ms": 0.0},
            "load": {"passed": True, "duration_ms": import_and_load_duration_ms, "device": device},
            "infer": {"passed": True, "duration_ms": infer_duration_ms, "device": device},
            "contract": {"passed": True, "duration_ms": 0.0, "segment_count": len(normalized)},
        },
        "result": normalized,
        "overall": "PASSED",
        "duration_seconds": round(time.time() - started, 3),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
