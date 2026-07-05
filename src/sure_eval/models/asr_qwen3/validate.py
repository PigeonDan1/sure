#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", MODEL_DIR / "artifacts")).resolve()
FIXTURE_DIR = MODEL_DIR / "fixture" / "asr"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
REQUIRE_GPU = os.environ.get("REQUIRE_GPU", "1") not in {"0", "false", "False"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(stage: str, status: str, message: str, **extra: Any) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": now_iso(), "stage": stage, "status": status, "message": message}
    payload.update(extra)
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def normalize_en(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9']+", " ", text.lower()).split())


def normalize_zh(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?;:：；“”\"'（）()《》<>-]+", "", text)


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score_asr(language: str, refs: list[str], hyps: list[str]) -> dict[str, Any]:
    if language == "zh":
        ref_units = [unit for text in refs for unit in normalize_zh(text)]
        hyp_units = [unit for text in hyps for unit in normalize_zh(text)]
        total = max(len(ref_units), 1)
        distance = edit_distance(ref_units, hyp_units)
        cer = distance / total
        return {"cer": cer, "details": {"score": cer, "metric": "cer", "edit_distance": distance, "all": total}}

    ref_units = [word for text in refs for word in normalize_en(text).split()]
    hyp_units = [word for text in hyps for word in normalize_en(text).split()]
    total = max(len(ref_units), 1)
    distance = edit_distance(ref_units, hyp_units)
    wer = distance / total
    return {"wer": wer, "details": {"score": wer, "metric": "wer", "edit_distance": distance, "all": total}}


def load_samples(subtask: str) -> list[dict[str, Any]]:
    root = FIXTURE_DIR / subtask
    manifest = root / "gt.jsonl"
    samples: list[dict[str, Any]] = []
    with manifest.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            audio_name = row.get("audio") or row.get("path")
            audio_path = root / audio_name
            if not audio_path.exists():
                raise FileNotFoundError(f"Missing fixture audio: {audio_path}")
            samples.append({**row, "_audio_path": str(audio_path)})
    if not samples:
        raise ValueError(f"No samples loaded from {manifest}")
    return samples


def main() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    if str(MODEL_DIR) not in sys.path:
        sys.path.insert(0, str(MODEL_DIR))

    import torch

    gpu_status = {
        "cuda_available": bool(torch.cuda.is_available()),
        "device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
        "device_names": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        if torch.cuda.is_available()
        else [],
        "require_gpu": REQUIRE_GPU,
    }
    if REQUIRE_GPU and not gpu_status["cuda_available"]:
        log("VALIDATE_GPU", "failed", "CUDA is required but unavailable.", gpu_status=gpu_status)
        raise RuntimeError("CUDA is required for Qwen3-ASR re-onboarding validation.")
    log("VALIDATE_GPU", "passed", "GPU preflight complete.", gpu_status=gpu_status)

    started = time.time()
    from model import ModelWrapper

    log("VALIDATE_IMPORT", "passed", "Imported ModelWrapper and torch.")

    wrapper = ModelWrapper(device=os.environ.get("DEVICE", "cuda"))
    wrapper.load()
    log("VALIDATE_LOAD", "passed", "Qwen3-ASR loaded.", health=wrapper.health())

    languages = {"asr_en": "English", "asr_zh": "Chinese"}
    report: dict[str, Any] = {
        "timestamp": now_iso(),
        "model_id": "Qwen/Qwen3-ASR-1.7B",
        "overall": "PASSED",
        "gpu_status": gpu_status,
        "languages": {},
    }

    for subtask, language_name in languages.items():
        samples = load_samples(subtask)
        refs: list[str] = []
        hyps: list[str] = []
        details: list[dict[str, Any]] = []
        subtask_started = time.time()
        for sample in samples:
            output = wrapper.predict({"audio_path": sample["_audio_path"], "language": language_name})
            text = output["text"]
            if not isinstance(text, str) or not text.strip():
                raise AssertionError(f"Contract failed for {sample.get('key')}: empty text")
            refs.append(str(sample.get("ground_truth") or sample.get("text") or ""))
            hyps.append(text)
            details.append(
                {
                    "key": sample.get("key"),
                    "audio": sample.get("audio") or sample.get("path"),
                    "ground_truth": refs[-1],
                    "prediction": text,
                }
            )

        language = subtask.replace("asr_", "")
        metrics = score_asr(language, refs, hyps)
        (ARTIFACTS_DIR / f"ref_{language}.txt").write_text(
            "\n".join(f"{d['key']}\t{d['ground_truth']}" for d in details) + "\n",
            encoding="utf-8",
        )
        (ARTIFACTS_DIR / f"hyp_{language}.txt").write_text(
            "\n".join(f"{d['key']}\t{d['prediction']}" for d in details) + "\n",
            encoding="utf-8",
        )
        report["languages"][language] = {
            "language": language,
            "num_samples": len(samples),
            "tests": {
                "import": {"passed": True},
                "load": {"passed": True},
                "infer": {"passed": True},
                "contract": {"passed": True},
                "evaluate": {"passed": True},
            },
            "metrics": metrics,
            "samples": details,
            "duration_seconds": round(time.time() - subtask_started, 3),
        }
        log("VALIDATE_INFER", "passed", f"Inference complete for {subtask}.", num_samples=len(samples))
        log("VALIDATE_EVAL", "passed", f"Metric complete for {subtask}.", metrics=metrics)

    report["duration_seconds"] = round(time.time() - started, 3)
    SAMPLE_OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("VALIDATE_CONTRACT", "passed", "All outputs satisfy ASR JSON contract.")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
