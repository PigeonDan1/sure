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
FIXTURE_DIR = MODEL_DIR / "fixture" / "asr" / "asr_zh"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(stage: str, status: str, message: str, **extra: Any) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": now_iso(), "stage": stage, "status": status, "message": message}
    payload.update(extra)
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


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


def cer(refs: list[str], hyps: list[str]) -> dict[str, Any]:
    ref_units = [unit for text in refs for unit in normalize_zh(text)]
    hyp_units = [unit for text in hyps for unit in normalize_zh(text)]
    total = max(len(ref_units), 1)
    distance = edit_distance(ref_units, hyp_units)
    score = distance / total
    return {"cer": score, "details": {"score": score, "metric": "cer", "edit_distance": distance, "all": total}}


def load_samples() -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with (FIXTURE_DIR / "gt.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            audio = row.get("audio") or row.get("path")
            audio_path = FIXTURE_DIR / audio
            if not audio_path.exists():
                raise FileNotFoundError(f"Missing fixture audio: {audio_path}")
            samples.append({**row, "_audio_path": str(audio_path)})
    if not samples:
        raise ValueError(f"No samples loaded from {FIXTURE_DIR}")
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
        "requested_device": os.environ.get("DEVICE", "auto"),
    }
    log("VALIDATE_GPU_PREFLIGHT", "passed", "GPU preflight complete.", gpu_status=gpu_status)

    from model import ModelWrapper

    log("VALIDATE_IMPORT", "passed", "Imported ModelWrapper and torch.")
    started = time.time()
    wrapper = ModelWrapper(device=os.environ.get("DEVICE", "auto"))
    wrapper.load()
    log("VALIDATE_LOAD", "passed", "Whisper model loaded.", health=wrapper.health())

    samples = load_samples()
    refs: list[str] = []
    hyps: list[str] = []
    details: list[dict[str, Any]] = []
    for sample in samples:
        output = wrapper.predict({"audio_path": sample["_audio_path"], "language": "zh"})
        text = output["text"]
        if not isinstance(text, str) or not text.strip():
            raise AssertionError(f"Contract failed for {sample.get('key')}: empty text")
        ref = str(sample.get("ground_truth") or sample.get("text") or "")
        refs.append(ref)
        hyps.append(text)
        details.append(
            {
                "key": sample.get("key"),
                "audio": sample.get("audio") or sample.get("path"),
                "ground_truth": ref,
                "prediction": text,
            }
        )

    metrics = cer(refs, hyps)
    (ARTIFACTS_DIR / "ref_zh.txt").write_text(
        "\n".join(f"{d['key']}\t{d['ground_truth']}" for d in details) + "\n",
        encoding="utf-8",
    )
    (ARTIFACTS_DIR / "hyp_zh.txt").write_text(
        "\n".join(f"{d['key']}\t{d['prediction']}" for d in details) + "\n",
        encoding="utf-8",
    )
    log("VALIDATE_INFER", "passed", "Inference complete for asr_zh.", num_samples=len(samples))
    log("VALIDATE_EVAL", "passed", "CER metric complete for asr_zh.", metrics=metrics)
    log("VALIDATE_CONTRACT", "passed", "Outputs satisfy ASR JSON contract.")

    report = {
        "timestamp": now_iso(),
        "model_id": "openai/whisper-large-v3-turbo",
        "overall": "PASSED",
        "gpu_status": gpu_status,
        "subtasks": {
            "asr_zh": {
                "subtask": "asr_zh",
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
                "duration_seconds": round(time.time() - started, 3),
            }
        },
    }
    SAMPLE_OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
