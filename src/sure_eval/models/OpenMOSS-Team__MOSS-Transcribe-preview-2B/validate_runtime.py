#!/usr/bin/env python3
"""Repo-native phase-1 validation for MOSS-Transcribe-preview-2B."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_ROOT = Path(__file__).resolve().parent
ARTIFACTS_DIR = MODEL_ROOT / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
DEFAULT_REPO = "OpenMOSS-Team/MOSS-Transcribe-preview-2B"
DEFAULT_REVISION = "c98175cb20e48bd9be4e95f6c85f2af18899f780"
DEFAULT_FIXTURE = MODEL_ROOT / "fixture" / "asr" / "asr_en" / "sample_1_367-130732-0006.wav"


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


def configure_runtime_env() -> None:
    runtime = MODEL_ROOT / ".runtime"
    defaults = {
        "HF_HOME": runtime / "hf-home",
        "HF_HUB_CACHE": runtime / "hf-home" / "hub",
        "TRANSFORMERS_CACHE": runtime / "hf-home" / "hub",
        "MPLCONFIGDIR": runtime / "matplotlib",
        "TMPDIR": runtime / "tmp",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, str(value))
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)


def import_components() -> dict[str, str]:
    import librosa
    import torch
    import transformers
    from huggingface_hub import hf_hub_download
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from transformers.dynamic_module_utils import get_class_from_dynamic_module

    return {
        "librosa": getattr(librosa, "__version__", "unknown"),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "hf_hub_download": hf_hub_download.__name__,
        "AutoModelForCausalLM": AutoModelForCausalLM.__name__,
        "AutoTokenizer": AutoTokenizer.__name__,
        "get_class_from_dynamic_module": get_class_from_dynamic_module.__name__,
    }


def device_name() -> str:
    import torch

    requested = os.environ.get("DEVICE", "cuda:0")
    if requested.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("Requested CUDA device but torch.cuda.is_available() is false")
    return requested


def resolved_model_path(repo: str, revision: str) -> str:
    snapshot = (
        Path(os.environ.get("HF_HUB_CACHE", str(MODEL_ROOT / ".runtime" / "hf-home" / "hub")))
        / "models--OpenMOSS-Team--MOSS-Transcribe-preview-2B"
        / "snapshots"
        / revision
    )
    if snapshot.exists():
        return str(snapshot)
    return repo


def load_components(repo: str, revision: str, device: str) -> dict[str, Any]:
    import torch
    from huggingface_hub import hf_hub_download
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from transformers.dynamic_module_utils import get_class_from_dynamic_module

    started = time.time()
    model_path = resolved_model_path(repo, revision)
    is_local = Path(model_path).exists()
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        revision=None if is_local else revision,
        dtype=torch.bfloat16,
        trust_remote_code=True,
        local_files_only=is_local,
    ).to(device).eval()

    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        revision=None if is_local else revision,
        trust_remote_code=True,
        local_files_only=is_local,
    )

    MossProcessor = get_class_from_dynamic_module(
        "processing_Moss.MossProcessor",
        model_path,
        revision=None if is_local else revision,
        local_files_only=is_local,
    )
    MelConfig = get_class_from_dynamic_module(
        "processing_Moss.MelConfig",
        model_path,
        revision=None if is_local else revision,
        local_files_only=is_local,
    )

    mel_cfg = MelConfig(
        mel_sr=16000,
        mel_dim=128,
        mel_n_fft=400,
        mel_hop_length=160,
    )
    processor = MossProcessor(
        tokenizer,
        config=mel_cfg,
        enable_time_marker=False,
    )
    if is_local:
        template_path = str(Path(model_path) / "chat_template_default.py")
    else:
        template_path = hf_hub_download(
            repo_id=repo,
            filename="chat_template_default.py",
            revision=revision,
        )
    processor.load_template(template_path)

    return {
        "model": model,
        "tokenizer": tokenizer,
        "processor": processor,
        "template_path": template_path,
        "model_path": model_path,
        "duration_ms": round((time.time() - started) * 1000, 3),
    }


def infer(loaded: dict[str, Any], fixture: Path, device: str) -> dict[str, Any]:
    import librosa
    import torch

    model = loaded["model"]
    processor = loaded["processor"]
    waveform, _ = librosa.load(str(fixture), sr=16000, mono=True)
    model_inputs = processor(audio=waveform, return_tensors="pt").to(device)
    model_inputs["audio_data"] = model_inputs["audio_data"].to(model.dtype)

    started = time.time()
    with torch.no_grad():
        output_ids = model.generate(
            **model_inputs,
            max_new_tokens=int(os.environ.get("MAX_NEW_TOKENS", "512")),
            do_sample=False,
            num_beams=1,
            use_cache=True,
            eos_token_id=[processor.end_token_id],
        )
    generated_ids = output_ids[:, model_inputs["input_ids"].shape[1] :]
    transcript = processor.batch_decode(
        generated_ids,
        skip_special_tokens=True,
    )[0].strip()
    if not transcript:
        raise AssertionError("MOSS returned an empty transcription")
    return {
        "text": transcript,
        "duration_ms": round((time.time() - started) * 1000, 3),
        "input_frames": int(len(waveform)),
    }


def validate_contract(result: dict[str, Any]) -> None:
    json.dumps(result, ensure_ascii=False)
    if "text" not in result:
        raise AssertionError("Missing required output field: text")
    if not isinstance(result["text"], str):
        raise AssertionError("Output field text must be a string")
    if not result["text"].strip():
        raise AssertionError("Output field text must be non-empty")


def run(stage: str) -> int:
    configure_runtime_env()
    repo = os.environ.get("MODEL_ID", DEFAULT_REPO)
    revision = os.environ.get("MODEL_REVISION", DEFAULT_REVISION)
    fixture = Path(os.environ.get("FIXTURE_AUDIO", str(DEFAULT_FIXTURE))).resolve()
    if not fixture.exists():
        raise FileNotFoundError(f"Missing fixture audio: {fixture}")

    if stage in {"import", "load", "infer", "contract", "all"}:
        started = time.time()
        versions = import_components()
        append_log(
            "VALIDATE_IMPORT",
            "passed",
            "Repo-native imports succeeded.",
            {"duration_ms": round((time.time() - started) * 1000, 3), "versions": versions},
        )
        if stage == "import":
            return 0

    device = device_name()
    loaded = load_components(repo, revision, device)
    append_log(
        "VALIDATE_LOAD",
        "passed",
        "Repo-native model, tokenizer, processor, and chat template loaded.",
        {
            "duration_ms": loaded["duration_ms"],
            "device": device,
            "template_path": loaded["template_path"],
            "model_path": loaded["model_path"],
        },
    )
    if stage == "load":
        return 0

    result = infer(loaded, fixture, device)
    append_log(
        "VALIDATE_INFER",
        "passed",
        "Greedy ASR inference returned non-empty text.",
        {
            "duration_ms": result["duration_ms"],
            "fixture": str(fixture),
            "text_preview": result["text"][:120],
        },
    )
    if stage == "infer":
        SAMPLE_OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0

    validate_contract(result)
    append_log(
        "VALIDATE_CONTRACT",
        "passed",
        "Output satisfies io_contract: json with non-empty text.",
        {"required_fields": ["text"], "nonempty_fields": ["text"]},
    )
    SAMPLE_OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["import", "load", "infer", "contract", "all"], default="all")
    args = parser.parse_args()
    try:
        return run(args.stage)
    except Exception as exc:
        append_log(f"VALIDATE_{args.stage.upper()}", "failed", str(exc), {"error_type": type(exc).__name__})
        print(f"validation failed at {args.stage}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
