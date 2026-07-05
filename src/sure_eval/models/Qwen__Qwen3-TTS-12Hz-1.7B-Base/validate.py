#!/usr/bin/env python3
"""Phase-1 validation for Qwen/Qwen3-TTS-12Hz-1.7B-Base."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODEL_DIR.parents[3]
ARTIFACTS_DIR = MODEL_DIR / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
REQUESTED_REF_AUDIO = REPO_ROOT / "tests" / "fixtures" / "shared" / "tts" / "en_ref.wav"
MODEL_LOCAL_REF_AUDIO = MODEL_DIR / "fixture" / "tts" / "en" / "voice_clone_ref_en.wav"
FIXTURE_OVERRIDE_REASON = (
    "Human continuation accepted model-local fixture after requested shared fixture was "
    "missing and tests/fixtures/shared/tts was not creatable."
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(stage: str, status: str, message: str, extra: dict[str, Any] | None = None) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
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


def record_failure(stage: str, failure_type: str, message: str, extra: dict[str, Any] | None = None) -> int:
    append_log(stage, "failed", message, {"failure_type": failure_type, **(extra or {})})
    return 1


def resolve_ref_audio() -> Path:
    if REQUESTED_REF_AUDIO.exists():
        return REQUESTED_REF_AUDIO
    if MODEL_LOCAL_REF_AUDIO.exists():
        append_log(
            "VALIDATE_SPEC",
            "warning",
            "Using model-local fixture under human override.",
            {
                "requested_ref_audio": str(REQUESTED_REF_AUDIO),
                "model_local_ref_audio": str(MODEL_LOCAL_REF_AUDIO),
                "fallback_allowed_by_current_spec": True,
                "override_reason": FIXTURE_OVERRIDE_REASON,
            },
        )
        return MODEL_LOCAL_REF_AUDIO
    raise FileNotFoundError(
        f"Neither requested nor model-local fixture is available: {REQUESTED_REF_AUDIO}; {MODEL_LOCAL_REF_AUDIO}"
    )


def _nonempty_wavs(wavs: Any) -> bool:
    if wavs is None:
        return False
    if hasattr(wavs, "numel"):
        return bool(wavs.numel() > 0)
    if hasattr(wavs, "size") and not isinstance(wavs, (list, tuple)):
        return bool(wavs.size > 0)
    if isinstance(wavs, (list, tuple)):
        return len(wavs) > 0 and _nonempty_wavs(wavs[0])
    return True


def validate_contract(result: Any) -> dict[str, Any]:
    wavs = getattr(result, "wavs", None)
    sample_rate = getattr(result, "sample_rate", None)
    if wavs is None and isinstance(result, dict):
        wavs = result.get("wavs")
        sample_rate = result.get("sample_rate")
    if not _nonempty_wavs(wavs):
        raise AssertionError("wavs is empty")
    if not isinstance(sample_rate, int) or sample_rate <= 0:
        raise AssertionError(f"sample_rate must be a positive integer: {sample_rate!r}")
    payload = result.to_dict() if hasattr(result, "to_dict") else {"wavs": "<non-json-audio>", "sample_rate": sample_rate}
    payload["json_serializable"] = False
    return payload


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    if VALIDATION_LOG.exists() and os.environ.get("SURE_APPEND_VALIDATION_LOG") != "1":
        VALIDATION_LOG.unlink()

    append_log(
        "VALIDATE_SPEC",
        "started",
        "Starting Qwen3-TTS 1.7B Base phase-1 validation.",
        {
            "requested_ref_audio": str(REQUESTED_REF_AUDIO),
            "model_local_ref_audio": str(MODEL_LOCAL_REF_AUDIO),
            "fallback_allowed_by_current_spec": True,
            "override_reason": FIXTURE_OVERRIDE_REASON,
        },
    )

    import_started = time.time()
    try:
        import torch  # noqa: F401
        from qwen_tts import Qwen3TTSModel  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return record_failure(
            "VALIDATE_IMPORT",
            "python_dependency_missing",
            f"Import test failed: {exc}",
            {"duration_ms": round((time.time() - import_started) * 1000, 3)},
        )
    append_log(
        "VALIDATE_IMPORT",
        "passed",
        "Imported qwen_tts.Qwen3TTSModel.",
        {"duration_ms": round((time.time() - import_started) * 1000, 3)},
    )

    if str(MODEL_DIR) not in sys.path:
        sys.path.insert(0, str(MODEL_DIR))
    from model import ModelWrapper

    load_started = time.time()
    wrapper = ModelWrapper(
        {
            "device_map": os.environ.get("DEVICE_MAP", "cuda:0"),
            "dtype": os.environ.get("TORCH_DTYPE", "bfloat16"),
            "attn_implementation": os.environ.get("ATTN_IMPLEMENTATION", "eager"),
        }
    )
    try:
        wrapper.load()
    except Exception as exc:  # noqa: BLE001
        return record_failure(
            "VALIDATE_LOAD",
            "runtime_backend_incompatible",
            f"Load test failed: {exc}",
            {
                "duration_ms": round((time.time() - load_started) * 1000, 3),
                "health": wrapper.healthcheck(),
            },
        )
    append_log(
        "VALIDATE_LOAD",
        "passed",
        "Loaded Qwen3-TTS 1.7B Base model.",
        {"duration_ms": round((time.time() - load_started) * 1000, 3), "health": wrapper.healthcheck()},
    )

    try:
        ref_audio = resolve_ref_audio()
    except Exception as exc:  # noqa: BLE001
        return record_failure(
            "VALIDATE_INFER",
            "fixture_unavailable",
            str(exc),
            {
                "requested_ref_audio": str(REQUESTED_REF_AUDIO),
                "model_local_ref_audio": str(MODEL_LOCAL_REF_AUDIO),
                "fallback_allowed_by_current_spec": True,
            },
        )

    infer_started = time.time()
    try:
        result = wrapper.predict(
            {
                "text": "Hello from Qwen3 TTS.",
                "language": "English",
                "ref_audio": str(ref_audio),
                "x_vector_only_mode": True,
                "max_new_tokens": 128,
                "output_path": str(ARTIFACTS_DIR / "outputs" / "qwen3_tts_1_7b_base_en_clone.wav"),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return record_failure(
            "VALIDATE_INFER",
            "runtime_backend_incompatible",
            f"Inference failed: {exc}",
            {"fixture_path": str(ref_audio), "duration_ms": round((time.time() - infer_started) * 1000, 3)},
        )
    append_log(
        "VALIDATE_INFER",
        "passed",
        "Voice clone inference produced audio.",
        {"duration_ms": round((time.time() - infer_started) * 1000, 3), "fixture_path": str(ref_audio)},
    )

    contract_started = time.time()
    try:
        payload = validate_contract(result)
    except Exception as exc:  # noqa: BLE001
        return record_failure("VALIDATE_CONTRACT", "wrapper_contract_mismatch", f"Contract failed: {exc}")
    SAMPLE_OUTPUT.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    append_log(
        "VALIDATE_CONTRACT",
        "passed",
        "Output satisfies wavs/sample_rate contract.",
        {"duration_ms": round((time.time() - contract_started) * 1000, 3), "sample_output": str(SAMPLE_OUTPUT)},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
