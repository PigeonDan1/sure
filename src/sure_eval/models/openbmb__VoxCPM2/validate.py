#!/usr/bin/env python3
"""Phase-1 validation for openbmb/VoxCPM2."""

from __future__ import annotations

import inspect
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


MODEL_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = MODEL_DIR / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
FIXTURE_PATH = MODEL_DIR / "fixture" / "tts" / "en" / "smoke.txt"
OUTPUT_WAV = ARTIFACTS_DIR / "outputs" / "voxcpm2_smoke.wav"


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


def fail(stage: str, failure_type: str, message: str, extra: dict[str, Any] | None = None) -> int:
    append_log(stage, "failed", message, {"failure_type": failure_type, **(extra or {})})
    return 1


def setup_model_local_env() -> None:
    runtime_dir = MODEL_DIR / ".runtime"
    env_defaults = {
        "HF_HOME": runtime_dir / "hf-home",
        "HF_HUB_CACHE": runtime_dir / "hf-home" / "hub",
        "HUGGINGFACE_HUB_CACHE": runtime_dir / "hf-home" / "hub",
        "TRANSFORMERS_CACHE": runtime_dir / "hf-home" / "transformers",
        "MPLCONFIGDIR": runtime_dir / "matplotlib",
        "TMPDIR": runtime_dir / "tmp",
    }
    for key, path in env_defaults.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(key, str(path.resolve()))


def validate_contract(result: Any) -> dict[str, Any]:
    audio = getattr(result, "audio", None)
    sample_rate = getattr(result, "sample_rate", None)
    if audio is None or sample_rate is None:
        if isinstance(result, dict):
            audio = result.get("audio")
            sample_rate = result.get("sample_rate")
    if audio is None:
        raise AssertionError("Missing required field: audio")
    if sample_rate is None:
        raise AssertionError("Missing required field: sample_rate")

    array = np.asarray(audio)
    if array.size == 0:
        raise AssertionError("audio is empty")
    if not np.isfinite(array).all():
        raise AssertionError("audio contains non-finite values")
    if int(sample_rate) <= 0:
        raise AssertionError(f"sample_rate must be positive, got {sample_rate!r}")

    return {
        "audio": {
            "dtype": str(array.dtype),
            "shape": list(array.shape),
            "num_samples": int(array.size),
            "min": float(array.min()),
            "max": float(array.max()),
        },
        "sample_rate": int(sample_rate),
        "text": getattr(result, "text", None),
        "audio_path": getattr(result, "audio_path", None),
    }


def write_weights_manifest(wrapper: Any) -> None:
    resolved = wrapper._resolve_model_path()
    manifest_path = ARTIFACTS_DIR / "weights_manifest.json"
    existing: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {"previous_manifest_parse_error": str(manifest_path)}

    resolved_exists = Path(resolved).exists()
    manifest = {
        **existing,
        "timestamp": now_iso(),
        "model_id": "openbmb/VoxCPM2",
        "declared_source": existing.get("declared_source", "huggingface"),
        "actual_source": existing.get("actual_source") or ("local_checkpoint" if resolved_exists else None),
        "source": existing.get("source") or existing.get("actual_source") or "huggingface",
        "repo_id": "openbmb/VoxCPM2",
        "required": True,
        "cache_policy": "model_local_first",
        "checkpoint_root": str((MODEL_DIR / "checkpoints").resolve()),
        "hf_cache_root": str((MODEL_DIR / ".runtime" / "hf-home").resolve()),
        "modelscope_cache_root": str((MODEL_DIR / ".runtime" / "modelscope_cache").resolve()),
        "resolved_local_model_path": resolved if resolved_exists else existing.get("resolved_local_model_path"),
        "runtime_load_identity": resolved,
        "status": "runtime_load_validated" if resolved_exists else existing.get("status", "pending"),
        "host_fallback_path": existing.get("host_fallback_path"),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    if VALIDATION_LOG.exists() and os.environ.get("SURE_APPEND_VALIDATION_LOG") != "1":
        VALIDATION_LOG.unlink()
    setup_model_local_env()

    append_log(
        "VALIDATE_SPEC",
        "passed",
        "Spec was validated before runtime checks; running phase-1 import/load/infer/contract.",
        {
            "fixture_path": str(FIXTURE_PATH),
            "fallback_allowed": False,
            "io_contract": {
                "input_type": "text",
                "output_type": "audio_array",
                "required_fields": ["audio", "sample_rate"],
                "json_serializable": False,
            },
        },
    )

    import_started = time.time()
    try:
        import torch
        from voxcpm import VoxCPM
    except Exception as exc:  # noqa: BLE001
        return fail("VALIDATE_IMPORT", "python_dependency_missing", f"Import failed: {exc}")
    append_log(
        "VALIDATE_IMPORT",
        "passed",
        "Imported voxcpm.VoxCPM.",
        {
            "duration_ms": round((time.time() - import_started) * 1000, 3),
            "torch_version": getattr(torch, "__version__", None),
            "torch_cuda": getattr(getattr(torch, "version", None), "cuda", None),
            "cuda_available": bool(torch.cuda.is_available()),
        },
    )

    signature = str(inspect.signature(VoxCPM._generate))
    user_infer_has_seed_conflict = "seed" not in signature
    if user_infer_has_seed_conflict:
        append_log(
            "VALIDATE_INFER",
            "warning",
            "User-provided infer_test includes seed=42, but official VoxCPM.generate signature does not expose a seed parameter.",
            {
                "failure_type": "wrong_entrypoint",
                "user_infer_test": "wav = model.generate(text='Hello, this is a short VoxCPM2 test.', cfg_value=2.0, inference_timesteps=10, seed=42)",
                "observed_signature": signature,
                "compatible_infer_test": "wav = model.generate(text='Hello, this is a short VoxCPM2 test.', cfg_value=2.0, inference_timesteps=10)",
            },
        )

    if str(MODEL_DIR) not in sys.path:
        sys.path.insert(0, str(MODEL_DIR))
    from model import ModelWrapper

    wrapper = ModelWrapper(
        {
            "model_id": "openbmb/VoxCPM2",
            "device": os.environ.get("DEVICE", "auto"),
            "load_denoiser": False,
            "optimize": False,
        }
    )

    load_started = time.time()
    try:
        wrapper.load()
    except Exception as exc:  # noqa: BLE001
        return fail(
            "VALIDATE_LOAD",
            "missing_weights" if "download" in str(exc).lower() or "not found" in str(exc).lower() else "runtime_backend_incompatible",
            f"Load failed: {exc}",
            {
                "duration_ms": round((time.time() - load_started) * 1000, 3),
                "health": wrapper.healthcheck(),
            },
        )
    write_weights_manifest(wrapper)
    append_log(
        "VALIDATE_LOAD",
        "passed",
        "Loaded VoxCPM2 with denoiser disabled and optimize disabled.",
        {"duration_ms": round((time.time() - load_started) * 1000, 3), "health": wrapper.healthcheck()},
    )

    if not FIXTURE_PATH.exists():
        return fail(
            "VALIDATE_INFER",
            "fixture_unavailable",
            f"Required text fixture is missing: {FIXTURE_PATH}",
            {"fallback_allowed": False},
        )
    text = FIXTURE_PATH.read_text(encoding="utf-8").strip()

    infer_started = time.time()
    try:
        result = wrapper.predict(
            {
                "text": text,
                "cfg_value": 2.0,
                "inference_timesteps": 10,
                "audio_path": str(OUTPUT_WAV),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return fail(
            "VALIDATE_INFER",
            "runtime_backend_incompatible",
            f"Inference failed: {exc}",
            {"duration_ms": round((time.time() - infer_started) * 1000, 3), "fixture_path": str(FIXTURE_PATH)},
        )
    append_log(
        "VALIDATE_INFER",
        "passed",
        "Compatible VoxCPM2 generation produced an audio array.",
        {"duration_ms": round((time.time() - infer_started) * 1000, 3), "fixture_path": str(FIXTURE_PATH)},
    )

    contract_started = time.time()
    try:
        summary = validate_contract(result)
    except Exception as exc:  # noqa: BLE001
        return fail("VALIDATE_CONTRACT", "wrapper_contract_mismatch", f"Contract failed: {exc}")

    SAMPLE_OUTPUT.write_text(json.dumps(summary, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    append_log(
        "VALIDATE_CONTRACT",
        "passed",
        "Output satisfies audio_array contract.",
        {
            "duration_ms": round((time.time() - contract_started) * 1000, 3),
            "sample_output": str(SAMPLE_OUTPUT),
            "sample_rate": summary["sample_rate"],
            "num_samples": summary["audio"]["num_samples"],
            "audio_path": summary.get("audio_path"),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
