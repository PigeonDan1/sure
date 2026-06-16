from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from model import ModelLoadError, ModelWrapper
from model import extract_choice_label
from sure_eval.evaluation.sure_evaluator import SUREEvaluator


ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = ROOT / "fixture"
ARTIFACTS = ROOT / "artifacts"
SLU_METADATA_PATH = ROOT / "fixture" / "slu" / "mmsu_metadata.json"


def load_slu_metadata() -> dict[str, dict[str, Any]]:
    if not SLU_METADATA_PATH.exists():
        return {}
    rows = json.loads(SLU_METADATA_PATH.read_text(encoding="utf-8"))
    return {row["key"]: row for row in rows}


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


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip().lower()


def edit_distance(reference: str, hypothesis: str) -> int:
    previous = list(range(len(hypothesis) + 1))
    for i, ref_char in enumerate(reference, start=1):
        current = [i]
        for j, hyp_char in enumerate(hypothesis, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (ref_char != hyp_char)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def extract_choice(text: str) -> str:
    return extract_choice_label(text) or ""


def build_slu_prompt(case: dict[str, Any]) -> str:
    return case.get("prompt", "")


def run_asr(wrapper: ModelWrapper, log_lines: list[str]) -> dict[str, Any]:
    cases = load_cases("ASR")
    outputs = []
    ref_rows: list[tuple[str, str]] = []
    hyp_rows: list[tuple[str, str]] = []
    total_distance = 0
    total_reference_chars = 0
    empty_predictions: list[str] = []
    for case in cases:
        result = wrapper.predict(case["audio_path"]).to_dict()
        prediction = result.get("text", "")
        reference = case["ground_truth"]
        normalized_reference = normalize_text(reference)
        normalized_prediction = normalize_text(prediction)
        distance = edit_distance(normalized_reference, normalized_prediction)
        total_distance += distance
        total_reference_chars += len(normalized_reference)
        ref_rows.append((case["key"], reference))
        hyp_rows.append((case["key"], prediction))
        outputs.append({**case, "prediction": prediction, "cer_distance": distance, "result": result})
        if not prediction.strip():
            empty_predictions.append(case["key"])
        status_word = "OK" if prediction.strip() else "MISSING_OUTPUT"
        log_lines.append(f"asr: {status_word} key={case['key']} cer_distance={distance}")
    ref_path = ARTIFACTS / "ref_asr.txt"
    hyp_path = ARTIFACTS / "hyp_asr.txt"
    write_key_text(ref_path, ref_rows)
    write_key_text(hyp_path, hyp_rows)
    cer = total_distance / total_reference_chars if total_reference_chars else 0.0
    return {
        "status": "COMPLETE",
        "num_samples": len(outputs),
        "metrics": {"cer": cer, "empty_predictions": empty_predictions},
        "outputs": outputs,
    }


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
        status_word = "OK" if prediction.strip() else "MISSING_OUTPUT"
        log_lines.append(f"s2tt: {status_word} key={case['key']} text_nonempty={bool(prediction.strip())}")
    ref_path = ARTIFACTS / "ref_s2tt.txt"
    hyp_path = ARTIFACTS / "hyp_s2tt.txt"
    write_key_text(ref_path, ref_rows)
    write_key_text(hyp_path, hyp_rows)
    metrics = SUREEvaluator(language="zh").evaluate("s2tt", str(ref_path), str(hyp_path))
    return {"status": "COMPLETE", "num_samples": len(outputs), "metrics": metrics, "outputs": outputs}


def run_slu(wrapper: ModelWrapper, log_lines: list[str]) -> dict[str, Any]:
    cases = load_cases("SLU")
    outputs = []
    ref_rows: list[tuple[str, str]] = []
    hyp_rows: list[tuple[str, str]] = []
    prompt_rows = []
    for case in cases:
        prompt = build_slu_prompt(case)
        result = wrapper.understand(case["audio_path"], prompt=prompt).to_dict()
        prediction = result.get("text", "")
        choice = result.get("label") or extract_choice(prediction)
        ref_rows.append((case["key"], case["ground_truth"]))
        hyp_rows.append((case["key"], choice or prediction))
        prompt_rows.append({"key": case["key"], "prompt": prompt})
        correct = choice == case["ground_truth"]
        outputs.append(
            {
                **case,
                "prediction": prediction,
                "normalized_prediction": choice,
                "correct": correct,
                "result": result,
            }
        )
        status_word = "OK" if correct else "MISMATCH"
        log_lines.append(
            f"slu: {status_word} key={case['key']} expected={case['ground_truth']} got={choice or prediction}"
        )
    ref_path = ARTIFACTS / "ref_slu.txt"
    hyp_path = ARTIFACTS / "hyp_slu.txt"
    prompt_path = ARTIFACTS / "prompt_slu.jsonl"
    write_key_text(ref_path, ref_rows)
    write_key_text(hyp_path, hyp_rows)
    prompt_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in prompt_rows),
        encoding="utf-8",
    )
    accuracy = SUREEvaluator(language="zh").evaluate("slu", str(ref_path), str(hyp_path), prompt_jsonl=str(prompt_path))
    metrics = {"accuracy": accuracy}
    return {
        "status": "COMPLETE",
        "num_samples": len(outputs),
        "metrics": metrics,
        "outputs": outputs,
    }


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
        correct = label == case["ground_truth"]
        ref_rows.append((case["key"], case["ground_truth"]))
        hyp_rows.append((case["key"], label or result.get("text", "")))
        outputs.append(
            {
                **case,
                "prediction": result.get("text", ""),
                "normalized_prediction": label,
                "correct": correct,
                "result": result,
            }
        )
        if label:
            status_word = "OK" if correct else "MISMATCH"
        else:
            status_word = "UNPARSED_LABEL"
        log_lines.append(f"ser: {status_word} key={case['key']} expected={case['ground_truth']} got={label or result.get('text', '')}")
    ref_path = ARTIFACTS / "ref_ser.txt"
    hyp_path = ARTIFACTS / "hyp_ser.txt"
    write_key_text(ref_path, ref_rows)
    write_key_text(hyp_path, hyp_rows)
    metrics = {"accuracy": SUREEvaluator(language="en").evaluate("ser", str(ref_path), str(hyp_path))}
    return {
        "status": "COMPLETE",
        "num_samples": len(outputs),
        "metrics": metrics,
        "outputs": outputs,
    }


def run_gr(wrapper: ModelWrapper, log_lines: list[str]) -> dict[str, Any]:
    cases = load_cases("GR")
    outputs = []
    ref_rows: list[tuple[str, str]] = []
    hyp_rows: list[tuple[str, str]] = []
    for case in cases:
        if not case.get("ground_truth"):
            raise RuntimeError(f"GR fixture missing ground_truth label for {case['key']}")
        result = wrapper.recognize_gender(case["audio_path"]).to_dict()
        label = result.get("label")
        correct = label == case["ground_truth"]
        ref_rows.append((case["key"], case["ground_truth"]))
        hyp_rows.append((case["key"], label or result.get("text", "")))
        outputs.append(
            {
                **case,
                "prediction": result.get("text", ""),
                "normalized_prediction": label,
                "correct": correct,
                "result": result,
            }
        )
        if label:
            status_word = "OK" if correct else "MISMATCH"
        else:
            status_word = "UNPARSED_LABEL"
        log_lines.append(f"gr: {status_word} key={case['key']} expected={case['ground_truth']} got={label or result.get('text', '')}")
    ref_path = ARTIFACTS / "ref_gr.txt"
    hyp_path = ARTIFACTS / "hyp_gr.txt"
    write_key_text(ref_path, ref_rows)
    write_key_text(hyp_path, hyp_rows)
    metrics = {"accuracy": SUREEvaluator(language="en").evaluate("gr", str(ref_path), str(hyp_path))}
    return {
        "status": "COMPLETE",
        "num_samples": len(outputs),
        "metrics": metrics,
        "outputs": outputs,
    }


def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    started = time.time()
    requested_tasks = [
        task.strip().upper()
        for task in os.environ.get("KIMI_AUDIO_VALIDATE_TASKS", "ASR,S2TT,SER,SLU,GR").split(",")
        if task.strip()
    ]
    run_task = {
        "ASR": run_asr,
        "S2TT": run_s2tt,
        "SER": run_ser,
        "SLU": run_slu,
        "GR": run_gr,
    }
    unknown_tasks = [task for task in requested_tasks if task not in run_task]
    if unknown_tasks:
        raise RuntimeError(f"Unknown validation tasks: {unknown_tasks}")
    log_lines = ["validate_multitask.py: START", f"tasks: {','.join(requested_tasks)}"]
    wrapper = ModelWrapper()
    log_lines.append(f"resolve_model_path: {wrapper._resolve_model_path()}")
    status = "COMPLETE"
    failure: str | None = None
    task_results: dict[str, Any] = {}
    try:
        wrapper.load()
        log_lines.append("load: OK")
        for task in requested_tasks:
            task_results[task] = run_task[task](wrapper, log_lines)
        log_lines.append(f"evaluation: COMPLETE tasks={requested_tasks}")
    except (ModelLoadError, Exception) as exc:
        status = "ERROR"
        failure = str(exc)
        log_lines.append(f"validate_multitask.py: ERROR {failure}")

    sample_output = {
        "status": status,
        "model": "asr_kimi_audio",
        "tasks": requested_tasks,
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
    return 0 if status == "COMPLETE" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / "validation_multitask.log").write_text(
            f"validate_multitask.py: ERROR\nerror: {exc}\n", encoding="utf-8"
        )
        print(f"multitask validation error: {exc}", file=sys.stderr)
        raise
