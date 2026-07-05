#!/usr/bin/env python3
"""Model-local wrapper validation for MOSS-Transcribe-preview-2B."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_ROOT = Path(__file__).resolve().parent
ARTIFACTS_DIR = MODEL_ROOT / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
FIXTURE_ROOT = MODEL_ROOT / "fixture" / "asr"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def append_log(stage: str, status: str, message: str, extra: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "timestamp": now_iso(),
        "stage": stage,
        "status": status,
        "message": message,
    }
    if extra:
        payload.update(extra)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for gt_path in sorted(FIXTURE_ROOT.glob("*/gt.jsonl")):
        for line in gt_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            audio_path = gt_path.parent / item["audio"]
            if not audio_path.exists():
                raise FileNotFoundError(f"Missing fixture audio: {audio_path}")
            item["_audio_path"] = str(audio_path)
            item["_fixture"] = str(gt_path.parent.relative_to(MODEL_ROOT))
            cases.append(item)
    if not cases:
        raise FileNotFoundError(f"No fixture cases found under {FIXTURE_ROOT}")
    return cases


def validate_contract(result: dict[str, Any]) -> None:
    json.dumps(result, ensure_ascii=False)
    if "text" not in result:
        raise AssertionError("Missing required output field: text")
    if not isinstance(result["text"], str):
        raise AssertionError("Output field text must be a string")
    if not result["text"].strip():
        raise AssertionError("Output field text must be non-empty")


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    outputs: list[dict[str, Any]] = []
    status = "PASSED"
    failure: dict[str, Any] | None = None
    try:
        import_started = time.time()
        from model import ModelWrapper

        append_log(
            "WRAPPER_IMPORT",
            "passed",
            "ModelWrapper import succeeded.",
            {"duration_ms": round((time.time() - import_started) * 1000, 3)},
        )
        wrapper = ModelWrapper()
        load_started = time.time()
        wrapper.load()
        append_log(
            "WRAPPER_LOAD",
            "passed",
            "ModelWrapper.load succeeded.",
            {"duration_ms": round((time.time() - load_started) * 1000, 3), "health": wrapper.healthcheck()},
        )
        for case in load_cases():
            infer_started = time.time()
            result = wrapper.predict({"audio_path": case["_audio_path"]}).to_dict()
            validate_contract(result)
            outputs.append(
                {
                    "key": case["key"],
                    "fixture": case["_fixture"],
                    "audio": case["audio"],
                    "ground_truth": case.get("ground_truth"),
                    "prediction": result["text"],
                    "raw": result.get("raw"),
                }
            )
            append_log(
                "WRAPPER_INFER",
                "passed",
                "Wrapper inference and contract passed.",
                {
                    "key": case["key"],
                    "duration_ms": round((time.time() - infer_started) * 1000, 3),
                    "text_preview": result["text"][:120],
                },
            )
    except Exception as exc:
        status = "FAILED"
        failure = {"type": type(exc).__name__, "message": str(exc)}
        append_log("WRAPPER_VALIDATE", "failed", str(exc), {"error_type": type(exc).__name__})

    report: dict[str, Any] = {
        "timestamp": now_iso(),
        "model_id": "OpenMOSS-Team/MOSS-Transcribe-preview-2B",
        "status": status,
        "num_samples": len(outputs),
        "duration_seconds": round(time.time() - started, 3),
        "failure": failure,
        "outputs": outputs,
    }
    SAMPLE_OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
