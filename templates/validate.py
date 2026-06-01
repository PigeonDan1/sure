#!/usr/bin/env python3
"""
Local Validation Script Template.

Loads fixture audio + gt.jsonl, runs inference through the model wrapper,
and evaluates metrics using SUREEvaluator.

Usage:
    cd src/sure_eval/models/<model_id>
    .venv/bin/python validate.py

Environment:
    MODEL_PATH  – model identifier or local path
    DEVICE      – cpu / cuda / auto (default: cpu)
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
FIXTURE_DIR = MODEL_ROOT / "fixture" / "__TASK_TYPE_LOWER__"  # e.g. "asr", "ser"
ARTIFACTS_DIR = MODEL_ROOT / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"

# ---------------------------------------------------------------------------
# Agent-filled constants (customize per model)
# ---------------------------------------------------------------------------
MODEL_ID = "__MODEL_ID__"  # e.g. "Qwen/Qwen3-ASR-1.7B"
WRAPPER_CLASS = "__WRAPPER_CLASS__"  # e.g. "ASRQwen3Model"
PREDICT_METHOD = "__PREDICT_METHOD__"  # e.g. "transcribe", "predict", "enhance"
TASK_TYPE = "__TASK_TYPE__"  # e.g. "ASR", "SER", "S2TT", "SD"
# Map sub-task directory names -> evaluation kwargs for SUREEvaluator
# Example for ASR:
#   {"asr_zh": {"language": "zh"}, "asr_en": {"language": "en"}}
SUBTASK_EVAL_KWARGS: dict[str, dict[str, Any]] = {
    # "__SUBTASK_NAME__": {"language": "__LANG__"},
}


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


def load_fixture(subtask: str) -> list[dict[str, Any]]:
    """Load gt.jsonl for a given sub-task and resolve absolute audio paths."""
    subdir = FIXTURE_DIR / subtask
    gt_path = subdir / "gt.jsonl"
    if not gt_path.exists():
        raise FileNotFoundError(f"Missing gt.jsonl for subtask={subtask}: {gt_path}")

    samples: list[dict[str, Any]] = []
    with gt_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            # Resolve audio path relative to gt.jsonl directory
            audio_path = subdir / item["audio"]
            if not audio_path.exists():
                raise FileNotFoundError(f"Missing audio file: {audio_path}")
            item["_audio_path"] = str(audio_path)
            samples.append(item)
    return samples


def discover_subtasks() -> list[str]:
    """Discover available sub-task fixtures under fixture/<task>/."""
    if not FIXTURE_DIR.exists():
        raise FileNotFoundError(f"Fixture directory missing: {FIXTURE_DIR}")
    return sorted(
        d.name
        for d in FIXTURE_DIR.iterdir()
        if d.is_dir() and (d / "gt.jsonl").exists()
    )


def run_validation(subtask: str) -> dict[str, Any]:
    """Run full validation pipeline for one sub-task fixture."""
    started = time.time()
    eval_kwargs = SUBTASK_EVAL_KWARGS.get(subtask, {})
    language = eval_kwargs.get("language", "en")

    # ------------------------------------------------------------------
    # 1. Load fixture
    # ------------------------------------------------------------------
    samples = load_fixture(subtask)
    if not samples:
        raise ValueError(f"No samples found in fixture for subtask={subtask}")

    # ------------------------------------------------------------------
    # 2. Import test
    # ------------------------------------------------------------------
    import_started = time.time()
    repo_src = str(REPO_ROOT / "src")
    if repo_src not in sys.path:
        sys.path.insert(0, repo_src)
    if str(MODEL_ROOT) not in sys.path:
        sys.path.insert(0, str(MODEL_ROOT))

    # Dynamic import of the wrapper class from model.py
    model_module = __import__("model", fromlist=[WRAPPER_CLASS])
    WrapperClass = getattr(model_module, WRAPPER_CLASS)
    import_duration_ms = round((time.time() - import_started) * 1000, 3)
    append_log("VALIDATE_IMPORT", "passed", f"Import ok for subtask={subtask}.")

    # ------------------------------------------------------------------
    # 3. Load model
    # ------------------------------------------------------------------
    load_started = time.time()
    model_path = os.environ.get("MODEL_PATH", MODEL_ID)
    device = os.environ.get("DEVICE", "cpu")
    model = WrapperClass(model_path=model_path, device=device)
    # Trigger lazy load if available
    if hasattr(model, "load"):
        model.load()
    load_duration_ms = round((time.time() - load_started) * 1000, 3)
    append_log("VALIDATE_LOAD", "passed", f"Model loaded for subtask={subtask}.", {
        "model_path": model_path,
        "device": device,
    })

    # ------------------------------------------------------------------
    # 4. Inference
    # ------------------------------------------------------------------
    infer_started = time.time()
    predictions: list[tuple[str, str]] = []
    predict_fn = getattr(model, PREDICT_METHOD)

    for sample in samples:
        key = sample["key"]
        audio_path = sample["_audio_path"]

        # Task-specific argument preparation (customize as needed)
        result = predict_fn(audio_path)

        # Extract text/label from result (adapt per task)
        if hasattr(result, "text"):
            pred_text = result.text
        elif hasattr(result, "label"):
            pred_text = result.label
        else:
            pred_text = str(result)
        predictions.append((key, pred_text))

    infer_duration_ms = round((time.time() - infer_started) * 1000, 3)
    append_log("VALIDATE_INFER", "passed", f"Inference ok for subtask={subtask}.", {
        "num_samples": len(samples),
    })

    # ------------------------------------------------------------------
    # 5. Prepare ref / hyp files for SUREEvaluator
    # ------------------------------------------------------------------
    ref_lines = [f"{s['key']}\t{s['ground_truth']}" for s in samples]
    hyp_lines = [f"{k}\t{t}" for k, t in predictions]

    ref_file = ARTIFACTS_DIR / f"ref_{subtask}.txt"
    hyp_file = ARTIFACTS_DIR / f"hyp_{subtask}.txt"
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
        "metrics = ev.evaluate('{task}', '{ref}', '{hyp}');"
        "print(json.dumps(metrics, ensure_ascii=False))"
    ).format(
        repo_src=str(REPO_ROOT / "src"),
        lang=language,
        task=TASK_TYPE,
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
    score = metrics.get("score", 0.0)
    append_log("VALIDATE_EVAL", "passed", f"score={score:.4f}", {
        "subtask": subtask,
        "metric": "score",
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
        "subtask": subtask,
        "num_samples": len(samples),
        "tests": {
            "import": {"passed": True, "duration_ms": import_duration_ms},
            "load": {"passed": True, "duration_ms": load_duration_ms},
            "infer": {"passed": True, "duration_ms": infer_duration_ms},
            "evaluate": {"passed": True, "duration_ms": eval_duration_ms},
        },
        "metrics": metrics,
        "samples": sample_details,
        "duration_seconds": duration_seconds,
    }


def main() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "timestamp": now_iso(),
        "model_id": MODEL_ID,
        "overall": "PASSED",
        "subtasks": {},
    }

    available = discover_subtasks()
    if not available:
        raise FileNotFoundError(f"No gt.jsonl fixtures found under {FIXTURE_DIR}")

    for subtask in available:
        try:
            subtask_result = run_validation(subtask)
            result["subtasks"][subtask] = subtask_result
        except Exception as exc:
            result["subtasks"][subtask] = {
                "error": str(exc),
                "overall": "FAILED",
            }
            result["overall"] = "FAILED"
            append_log("VALIDATE_RUN", "failed", str(exc), {"subtask": subtask})

    SAMPLE_OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
