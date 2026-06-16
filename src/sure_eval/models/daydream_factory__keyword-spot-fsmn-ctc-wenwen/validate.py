#!/usr/bin/env python3
"""Validate the local WekWS KWS wrapper on a small fixture."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from model import DEFAULT_KEYWORDS, WeKWSModel
import torch


MODEL_ROOT = Path(__file__).resolve().parent
FIXTURE_JSONL = MODEL_ROOT / "fixture" / "kws" / "gt.jsonl"
ARTIFACTS_DIR = MODEL_ROOT / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
VERDICT_JSON = ARTIFACTS_DIR / "verdict.json"


def _default_gpu() -> int:
    if "WEKWS_GPU" in os.environ:
        return int(os.environ["WEKWS_GPU"])
    return 0 if torch.cuda.is_available() else -1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(stage: str, status: str, message: str, **extra: Any) -> None:
    payload: dict[str, Any] = {
        "timestamp": _now(),
        "stage": stage,
        "status": status,
        "message": message,
    }
    payload.update(extra)
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_fixture() -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with FIXTURE_JSONL.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            item["_audio_path"] = str(FIXTURE_JSONL.parent / item["audio"])
            samples.append(item)
    return samples


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    VALIDATION_LOG.write_text("", encoding="utf-8")
    started = time.time()

    try:
        samples = _load_fixture()
        if not samples:
            raise ValueError(f"No KWS fixture samples in {FIXTURE_JSONL}")
        for sample in samples:
            if not Path(sample["_audio_path"]).exists():
                raise FileNotFoundError(sample["_audio_path"])
        _append_log("FIXTURE", "passed", "Fixture loaded.", num_samples=len(samples))

        model = WeKWSModel(
            keywords=os.environ.get("WEKWS_KEYWORDS", DEFAULT_KEYWORDS),
            threshold=float(os.environ.get("WEKWS_THRESHOLD", "0.0")),
            gpu=_default_gpu(),
        )
        _append_log(
            "LOAD",
            "passed",
            "Wrapper constructed.",
            keywords=model.keywords,
            gpu=model.gpu,
        )

        outputs: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []
        for sample in samples:
            result = model.predict(sample["_audio_path"])
            output = {
                "key": sample["key"],
                "audio": sample["audio"],
                "expected": sample.get("expected"),
                "result": result.to_dict(),
            }
            outputs.append(output)
            expected = sample.get("expected")
            if expected == "detect" and not result.detected:
                mismatches.append(output)
            if expected == "reject" and result.detected:
                mismatches.append(output)
        SAMPLE_OUTPUT.write_text(
            json.dumps(outputs, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if mismatches:
            raise AssertionError(
                "KWS fixture expectation mismatch: "
                + json.dumps(mismatches, ensure_ascii=False)
            )
        _append_log("INFER", "passed", "Inference completed.", outputs=outputs)

        verdict = {
            "status": "passed",
            "timestamp": _now(),
            "duration_ms": round((time.time() - started) * 1000, 3),
            "task": "KWS",
            "backend": "uv",
            "gpu": model.gpu,
            "cuda_available": torch.cuda.is_available(),
            "decoder": "offline_ctc_prefix_beam_search",
            "weights_location": str(
                MODEL_ROOT
                / ".runtime"
                / "modelscope_cache"
                / "daydream-factory"
                / "keyword-spot-fsmn-ctc-wenwen"
            ),
            "note": "Mobvoi positive fixture detects 嗨小问 and negative fixture is rejected.",
        }
        VERDICT_JSON.write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return 0
    except Exception as exc:
        _append_log("VALIDATE", "failed", str(exc), error_type=type(exc).__name__)
        verdict = {
            "status": "failed",
            "timestamp": _now(),
            "duration_ms": round((time.time() - started) * 1000, 3),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        VERDICT_JSON.write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
