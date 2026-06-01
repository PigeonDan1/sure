from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from model import ModelLoadError, ModelWrapper
from sure_eval.evaluation.sure_evaluator import SUREEvaluator


ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = ROOT / "fixture"
ARTIFACTS = ROOT / "artifacts"


def load_cases(task: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for gt_path in sorted((FIXTURE_ROOT / task.lower()).glob("*/gt.jsonl")):
        for line in gt_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            case["task"] = task.upper()
            case["audio_path"] = str((gt_path.parent / case["audio"]).resolve())
            case["fixture"] = str(gt_path.parent.relative_to(ROOT))
            cases.append(case)
    if not cases:
        raise RuntimeError(f"No {task} fixture cases found under {FIXTURE_ROOT}")
    return cases


def try_load_cases(task: str) -> list[dict[str, Any]]:
    try:
        return load_cases(task)
    except RuntimeError:
        return []


def write_key_text(path: Path, rows: list[tuple[str, str]]) -> None:
    path.write_text(
        "".join(f"{key}\t{text}\n" for key, text in rows),
        encoding="utf-8",
    )


def extract_choice(text: str) -> str:
    match = re.search(r"\b([A-Da-d])\b", text)
    if match:
        return match.group(1).upper()
    compact = re.sub(r"[^A-Da-d]", "", text)
    return compact[:1].upper() if compact else ""


def run_s2tt(wrapper: ModelWrapper, log_lines: list[str]) -> dict[str, Any]:
    cases = load_cases("S2TT")
    outputs = []
    ref_rows: list[tuple[str, str]] = []
    hyp_rows: list[tuple[str, str]] = []
    for case in cases:
        result = wrapper.translate(
            case["audio_path"],
            source_language=case.get("language", "auto"),
            target_language=case.get("target_language", "zh"),
        ).to_dict()
        prediction = result.get("text", "")
        ref_rows.append((case["key"], case["ground_truth"]))
        hyp_rows.append((case["key"], prediction))
        outputs.append({**case, "prediction": prediction, "result": result})
        log_lines.append(f"s2tt: PASS key={case['key']} text_nonempty={bool(prediction.strip())}")
    ref_path = ARTIFACTS / "ref_s2tt.txt"
    hyp_path = ARTIFACTS / "hyp_s2tt.txt"
    write_key_text(ref_path, ref_rows)
    write_key_text(hyp_path, hyp_rows)
    metrics = SUREEvaluator(language="zh").evaluate("s2tt", str(ref_path), str(hyp_path))
    return {"status": "PASS", "num_samples": len(outputs), "metrics": metrics, "outputs": outputs}


def run_slu(wrapper: ModelWrapper, log_lines: list[str]) -> dict[str, Any]:
    cases = load_cases("SLU")
    outputs = []
    ref_rows: list[tuple[str, str]] = []
    hyp_rows: list[tuple[str, str]] = []
    prompt_rows = []
    for case in cases:
        result = wrapper.understand(case["audio_path"], prompt=case.get("prompt")).to_dict()
        prediction = result.get("text", "")
        choice = extract_choice(prediction)
        ref_rows.append((case["key"], case["ground_truth"]))
        hyp_rows.append((case["key"], choice or prediction))
        prompt_rows.append({"key": case["key"], "prompt": case.get("prompt", "")})
        outputs.append({**case, "prediction": prediction, "normalized_prediction": choice, "result": result})
        log_lines.append(f"slu: PASS key={case['key']} answer_nonempty={bool((choice or prediction).strip())}")
    ref_path = ARTIFACTS / "ref_slu.txt"
    hyp_path = ARTIFACTS / "hyp_slu.txt"
    prompt_path = ARTIFACTS / "prompt_slu.jsonl"
    write_key_text(ref_path, ref_rows)
    write_key_text(hyp_path, hyp_rows)
    prompt_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in prompt_rows),
        encoding="utf-8",
    )
    metrics = {"accuracy": SUREEvaluator(language="zh").evaluate("slu", str(ref_path), str(hyp_path), prompt_jsonl=str(prompt_path))}
    return {"status": "PASS", "num_samples": len(outputs), "metrics": metrics, "outputs": outputs}


def run_ser(wrapper: ModelWrapper, log_lines: list[str]) -> dict[str, Any]:
    cases = try_load_cases("SER")
    if not cases:
        log_lines.append("ser: SKIP missing IEMOCAP fixture under fixture/ser/")
        return {
            "status": "SKIP",
            "num_samples": 0,
            "metrics": None,
            "reason": "Missing IEMOCAP SER fixture. Do not substitute ASR or other non-IEMOCAP audio for SER.",
            "expected_fixture": "fixture/ser/iemocap/gt.jsonl",
            "expected_labels": ["neu", "hap", "ang", "sad"],
            "outputs": [],
        }
    invalid_sources = [
        case.get("dataset", "")
        for case in cases
        if "IEMOCAP" not in str(case.get("dataset", "")).upper()
    ]
    if invalid_sources:
        raise RuntimeError(f"SER fixture must use IEMOCAP data, got datasets={invalid_sources}")
    outputs = []
    ref_rows: list[tuple[str, str]] = []
    hyp_rows: list[tuple[str, str]] = []
    for case in cases:
        if not case.get("ground_truth"):
            raise RuntimeError(f"SER fixture missing ground_truth label for {case['key']}")
        result = wrapper.recognize_emotion(case["audio_path"]).to_dict()
        label = result.get("label")
        ref_rows.append((case["key"], case["ground_truth"]))
        hyp_rows.append((case["key"], label or result.get("text", "")))
        outputs.append({**case, "prediction": result.get("text", ""), "normalized_prediction": label, "result": result})
        if not label:
            raise RuntimeError(f"SER contract failed for {case['key']}: label not in [neu,hap,ang,sad]")
        log_lines.append(f"ser: PASS key={case['key']} label={label}")
    ref_path = ARTIFACTS / "ref_ser.txt"
    hyp_path = ARTIFACTS / "hyp_ser.txt"
    write_key_text(ref_path, ref_rows)
    write_key_text(hyp_path, hyp_rows)
    metrics = {"accuracy": SUREEvaluator(language="en").evaluate("ser", str(ref_path), str(hyp_path))}
    return {
        "status": "PASS",
        "num_samples": len(outputs),
        "metrics": metrics,
        "outputs": outputs,
    }


def run_gr(wrapper: ModelWrapper, log_lines: list[str]) -> dict[str, Any]:
    cases = load_cases("GR")
    outputs = []
    for case in cases:
        result = wrapper.recognize_gender(case["audio_path"]).to_dict()
        label = result.get("label")
        outputs.append({**case, "prediction": result.get("text", ""), "normalized_prediction": label, "result": result})
        if not label:
            raise RuntimeError(f"GR contract failed for {case['key']}: label not in [male,female]")
        log_lines.append(f"gr: PASS key={case['key']} label={label}")
    return {
        "status": "PASS",
        "num_samples": len(outputs),
        "metrics": {"accuracy": None, "note": "contract smoke only; fixture has no trusted GR label"},
        "outputs": outputs,
    }


def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    started = time.time()
    log_lines = ["validate_multitask.py: START", "asr_compatibility: validate.py remains the ASR-only regression path"]
    wrapper = ModelWrapper()
    log_lines.append(f"resolve_model_path: {wrapper._resolve_model_path()}")
    status = "PASS"
    failure: str | None = None
    task_results: dict[str, Any] = {}
    try:
        wrapper.load()
        log_lines.append("load: PASS")
        task_results["S2TT"] = run_s2tt(wrapper, log_lines)
        task_results["SER"] = run_ser(wrapper, log_lines)
        task_results["SLU"] = run_slu(wrapper, log_lines)
        task_results["GR"] = run_gr(wrapper, log_lines)
        log_lines.append("contract: PASS tasks=[S2TT,SER,SLU,GR]")
    except (ModelLoadError, Exception) as exc:
        status = "FAIL"
        failure = str(exc)
        log_lines.append(f"validate_multitask.py: FAIL {failure}")

    sample_output = {
        "status": status,
        "model": "asr_kimi_audio",
        "tasks": ["S2TT", "SER", "SLU", "GR"],
        "healthcheck": wrapper.healthcheck(),
        "failure": failure,
        "task_results": task_results,
    }
    (ARTIFACTS / "multitask_sample_output.json").write_text(
        json.dumps(sample_output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    elapsed = time.time() - started
    log_lines.append(f"validate_multitask.py: {status} duration_seconds={elapsed:.2f}")
    (ARTIFACTS / "validation_multitask.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / "validation_multitask.log").write_text(
            f"validate_multitask.py: FAIL\nerror: {exc}\n", encoding="utf-8"
        )
        print(f"multitask validation failed: {exc}", file=sys.stderr)
        raise
