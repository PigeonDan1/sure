#!/usr/bin/env python3
"""
FireRedASR-LLM-L Local Validation Script.

Loads fixture audio + gt.jsonl, runs inference through ModelWrapper,
and evaluates WER/CER using SUREEvaluator.

Usage:
    cd src/sure_eval/models/asr_fireredasr
    .venv/bin/python validate.py

Environment:
    FIREREDASR_MODEL_PATH  – model directory (default: checkpoints/pretrained_models/fireredasr_llm_l)
    DEVICE                 – cpu / cuda / auto (default: cpu)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MODEL_ROOT.parents[3]
FIXTURE_DIR = MODEL_ROOT / "fixture" / "asr"
ARTIFACTS_DIR = MODEL_ROOT / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"


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
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with VALIDATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_fixture(language: str) -> list[dict[str, Any]]:
    """Load gt.jsonl for a given language and resolve absolute audio paths."""
    subdir = FIXTURE_DIR / f"asr_{language}"
    gt_path = subdir / "gt.jsonl"
    if not gt_path.exists():
        raise FileNotFoundError(f"Missing gt.jsonl for language={language}: {gt_path}")

    samples: list[dict[str, Any]] = []
    with gt_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            audio_path = subdir / item["audio"]
            if not audio_path.exists():
                raise FileNotFoundError(f"Missing audio file: {audio_path}")
            item["_audio_path"] = str(audio_path)
            samples.append(item)
    return samples


def run_validation(language: str) -> dict[str, Any]:
    """Run full validation pipeline for one language fixture."""
    started = time.time()

    # ------------------------------------------------------------------
    # 1. Load fixture
    # ------------------------------------------------------------------
    samples = load_fixture(language)
    if not samples:
        raise ValueError(f"No samples found in fixture for language={language}")

    # ------------------------------------------------------------------
    # 2. Import test
    # ------------------------------------------------------------------
    import_started = time.time()
    repo_src = str(REPO_ROOT / "src")
    if repo_src not in sys.path:
        sys.path.insert(0, repo_src)
    if str(MODEL_ROOT) not in sys.path:
        sys.path.insert(0, str(MODEL_ROOT))

    from model import ModelWrapper
    import_duration_ms = round((time.time() - import_started) * 1000, 3)
    append_log("VALIDATE_IMPORT", "passed", f"Import ok for language={language}.")

    # ------------------------------------------------------------------
    # 3. Load model
    # ------------------------------------------------------------------
    load_started = time.time()
    model_path = os.environ.get("FIREREDASR_MODEL_PATH", str(
        MODEL_ROOT / "checkpoints" / "pretrained_models" / "fireredasr_llm_l"
    ))
    device = os.environ.get("DEVICE", "cpu")
    model = ModelWrapper(model_path=model_path, device=device)
    model.load()
    load_duration_ms = round((time.time() - load_started) * 1000, 3)
    append_log("VALIDATE_LOAD", "passed", f"Model loaded for language={language}.", {
        "model_path": model_path,
        "device": device,
    })

    # ------------------------------------------------------------------
    # 4. Inference
    # ------------------------------------------------------------------
    infer_started = time.time()
    predictions: list[tuple[str, str]] = []

    for sample in samples:
        key = sample["key"]
        audio_path = sample["_audio_path"]

        result = model.transcribe(audio_path)
        predictions.append((key, result.text))

    infer_duration_ms = round((time.time() - infer_started) * 1000, 3)
    append_log("VALIDATE_INFER", "passed", f"Inference ok for language={language}.", {
        "num_samples": len(samples),
    })

    # ------------------------------------------------------------------
    # 5. Prepare ref / hyp files for SUREEvaluator
    # ------------------------------------------------------------------
    ref_lines = [f"{s['key']}\t{s['ground_truth']}" for s in samples]
    hyp_lines = [f"{k}\t{t}" for k, t in predictions]

    ref_file = ARTIFACTS_DIR / f"ref_{language}.txt"
    hyp_file = ARTIFACTS_DIR / f"hyp_{language}.txt"
    ref_file.write_text("\n".join(ref_lines) + "\n", encoding="utf-8")
    hyp_file.write_text("\n".join(hyp_lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # 6. Evaluate with SUREEvaluator (via root venv to avoid dep mismatch)
    # ------------------------------------------------------------------
    eval_started = time.time()
    root_venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if not root_venv_python.exists():
        raise FileNotFoundError(
            f"Root project venv not found at {root_venv_python}. "
            "Cannot run SUREEvaluator."
        )

    eval_script = (
        "import json, sys;"
        "sys.path.insert(0, str(__import__('pathlib').Path('{repo_src}')));"
        "from sure_eval.evaluation.sure_evaluator import SUREEvaluator;"
        "ev = SUREEvaluator(language='{lang}');"
        "metrics = ev.evaluate('ASR', '{ref}', '{hyp}');"
        "print(json.dumps(metrics, ensure_ascii=False))"
    ).format(
        repo_src=str(REPO_ROOT / "src"),
        lang=language,
        ref=str(ref_file),
        hyp=str(hyp_file),
    )

    proc = subprocess.run(
        [str(root_venv_python), "-c", eval_script],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"SUREEvaluator failed: {proc.stderr.strip()}")
    metrics = json.loads(proc.stdout.strip())

    eval_duration_ms = round((time.time() - eval_started) * 1000, 3)
    metric_name = "cer" if language == "zh" else "wer"
    score = metrics.get(metric_name, metrics.get("score"))
    append_log("VALIDATE_EVAL", "passed", f"{metric_name.upper()}={score:.4f}", {
        "language": language,
        "metric": metric_name,
        "score": score,
    })

    # ------------------------------------------------------------------
    # 7. Build per-sample detail
    # ------------------------------------------------------------------
    sample_details = [
        {
            "key": s["key"],
            "audio": s["audio"],
            "ground_truth": s["ground_truth"],
            "prediction": p[1],
        }
        for s, p in zip(samples, predictions)
    ]

    duration_seconds = round(time.time() - started, 3)

    return {
        "language": language,
        "num_samples": len(samples),
        "tests": {
            "import": {"passed": True, "duration_ms": import_duration_ms},
            "load": {"passed": True, "duration_ms": load_duration_ms},
            "infer": {"passed": True, "duration_ms": infer_duration_ms},
            "evaluate": {"passed": True, "duration_ms": eval_duration_ms},
        },
        "metrics": {
            metric_name: score,
            "details": metrics,
        },
        "samples": sample_details,
        "duration_seconds": duration_seconds,
    }


def main() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "timestamp": now_iso(),
        "model_id": "FireRedTeam/FireRedASR-LLM-L",
        "overall": "PASSED",
        "languages": {},
    }

    if not FIXTURE_DIR.exists():
        raise FileNotFoundError(f"Fixture directory missing: {FIXTURE_DIR}")

    available = sorted(
        d.name.replace("asr_", "")
        for d in FIXTURE_DIR.iterdir()
        if d.is_dir() and d.name.startswith("asr_") and (d / "gt.jsonl").exists()
    )

    if not available:
        raise FileNotFoundError(f"No gt.jsonl fixtures found under {FIXTURE_DIR}")

    for language in available:
        try:
            lang_result = run_validation(language)
            result["languages"][language] = lang_result
        except Exception as exc:
            result["languages"][language] = {
                "error": str(exc),
                "overall": "FAILED",
            }
            result["overall"] = "FAILED"
            append_log("VALIDATE_RUN", "failed", str(exc), {"language": language})

    SAMPLE_OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
