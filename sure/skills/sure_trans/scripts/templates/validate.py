#!/usr/bin/env python3
"""Model-local validation template for SURE /sure_trans.

Keeps the same CLI contract as the /sure_onboard template:

    python validate.py --stage import
    python validate.py --stage load
    python validate.py --stage infer
    python validate.py --stage contract
    python validate.py --stage all

Each stage writes artifacts/<stage>_result.json. Inference writes
artifacts/sample_output.json, and contract validates that sample against the
filled IO_CONTRACT constant. Set SURE_VALIDATE_ARTIFACTS_DIR to redirect the
artifacts directory (in-container runs mount the run artifacts there).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = Path(os.environ.get("SURE_VALIDATE_ARTIFACTS_DIR") or (MODEL_DIR / "artifacts"))
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"

# Agent-filled constants.
MODEL_ID = "__MODEL_NAME__"
TASK_TYPE = "__TASK_TYPE__"
WRAPPER_MODULE = "model"
WRAPPER_CLASS = "ModelWrapper"
_IO_CONTRACT_JSON = r'''__IO_CONTRACT_JSON__'''
IO_CONTRACT: dict[str, Any] = (
    {} if _IO_CONTRACT_JSON.startswith("__") else json.loads(_IO_CONTRACT_JSON)
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def result_path(stage: str) -> Path:
    return ARTIFACTS_DIR / f"{stage}_result.json"


def write_stage_result(stage: str, passed: bool, started: float, error: str | None = None, **extra: Any) -> None:
    key = f"{stage}_passed" if stage != "contract" else "contract_passed"
    payload: dict[str, Any] = {
        key: passed,
        "duration_ms": round((time.time() - started) * 1000, 3),
        "error": error,
        "model_dir": str(MODEL_DIR),
        "validate_py": "validate.py",
        "validate_args": ["--stage", stage],
        "sample_output_path": "artifacts/sample_output.json",
    }
    payload.update(extra)
    write_json(result_path(stage), payload)


def import_wrapper_class():
    if str(MODEL_DIR) not in sys.path:
        sys.path.insert(0, str(MODEL_DIR))
    module = __import__(WRAPPER_MODULE)
    return getattr(module, WRAPPER_CLASS)


def instantiate_wrapper() -> Any:
    wrapper_cls = import_wrapper_class()
    return wrapper_cls()


def load_wrapper() -> Any:
    wrapper = instantiate_wrapper()
    if hasattr(wrapper, "load"):
        wrapper.load()
    return wrapper


def first_fixture_payload() -> dict[str, Any]:
    raw_payload = os.environ.get("SURE_VALIDATE_INPUT_JSON")
    if raw_payload:
        parsed = json.loads(raw_payload)
        if not isinstance(parsed, dict):
            raise ValueError("SURE_VALIDATE_INPUT_JSON must decode to an object.")
        return parsed

    fixture_root = MODEL_DIR / "fixture"
    for gt_path in sorted(fixture_root.glob("**/gt.jsonl")):
        for line in gt_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                continue
            payload: dict[str, Any] = {}
            audio = item.get("audio") or item.get("wav") or item.get("prompt_audio") or item.get("reference_audio")
            if isinstance(audio, str):
                payload["audio_path"] = str((gt_path.parent / audio).resolve())
                payload["prompt_audio_path"] = payload["audio_path"]
                payload["reference_audio_path"] = payload["audio_path"]
                payload["ref_audio"] = payload["audio_path"]
            text = item.get("target_text") or item.get("text") or item.get("prompt_text") or item.get("ground_truth")
            if isinstance(text, str):
                payload["text"] = text
                payload["prompt_text"] = item.get("prompt_text", text)
            if isinstance(item.get("language"), str):
                payload["language"] = item["language"]
            if payload:
                return payload
    raise FileNotFoundError(
        "No validation payload found. Set SURE_VALIDATE_INPUT_JSON or provide fixture/**/gt.jsonl."
    )


def to_plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return to_plain(value.to_dict())
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "shape"):
        return {"type": type(value).__name__, "shape": list(value.shape)}
    return {"type": type(value).__name__, "repr": repr(value)[:500]}


def run_predict(wrapper: Any, payload: dict[str, Any]) -> dict[str, Any]:
    predict = getattr(wrapper, "predict", None)
    if predict is None:
        raise AttributeError("Wrapper has no 'predict' method.")
    try:
        result = predict(payload)
    except TypeError:
        if "audio_path" in payload:
            result = predict(payload["audio_path"])
        elif "text" in payload:
            result = predict(payload["text"])
        else:
            raise
    plain = to_plain(result)
    if isinstance(plain, dict):
        return plain
    if isinstance(plain, str):
        return {"text": plain}
    return {"result": plain}


def load_io_contract() -> dict[str, Any]:
    if IO_CONTRACT:
        return IO_CONTRACT
    raise FileNotFoundError("IO_CONTRACT was not filled during adapter scaffolding.")


def string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def vad_fixture_duration() -> float:
    raw_payload = os.environ.get("SURE_VALIDATE_INPUT_JSON")
    if raw_payload:
        payload = json.loads(raw_payload)
        value = payload.get("duration") if isinstance(payload, dict) else None
    else:
        value = None
        for gt_path in sorted((MODEL_DIR / "fixture").glob("**/gt.jsonl")):
            for line in gt_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    value = row.get("duration") if isinstance(row, dict) else None
                    break
            if value is not None:
                break
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("VAD fixture requires a numeric duration for contract validation")
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("VAD fixture duration must be positive and finite")
    return duration


def validate_vad_intervals(
    value: Any,
    *,
    field: str,
    duration: float,
    required_fields: tuple[str, ...],
) -> list[str]:
    if not isinstance(value, list):
        return [f"{field} must be a list"]
    violations: list[str] = []
    previous_end = 0.0
    allowed_fields = set(required_fields)
    for index, interval in enumerate(value):
        if not isinstance(interval, dict):
            violations.append(f"{field}[{index}] must be an object")
            continue
        unknown = sorted(set(interval) - allowed_fields)
        if unknown:
            violations.append(f"{field}[{index}] has unsupported fields: {', '.join(unknown)}")
        values = [interval.get(name) for name in required_fields]
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in values):
            violations.append(f"{field}[{index}] {', '.join(required_fields)} must be numeric")
            continue
        numeric = [float(item) for item in values]
        if not all(math.isfinite(item) for item in numeric):
            violations.append(f"{field}[{index}] values must be finite")
            continue
        start_value, end_value = numeric[:2]
        if start_value < 0 or start_value >= end_value or end_value > duration:
            violations.append(f"{field}[{index}] must satisfy 0 <= start < end <= duration")
        elif index > 0 and start_value < previous_end:
            violations.append(f"{field} must be sorted and non-overlapping")
        previous_end = end_value
    return violations


def validate_contract(
    sample: dict[str, Any],
    contract: dict[str, Any],
    vad_duration: float | None = None,
) -> list[str]:
    violations: list[str] = []
    for field in string_list(contract.get("required_fields")):
        if field not in sample:
            violations.append(f"missing required field: {field}")
    for field in string_list(contract.get("nonempty_fields")):
        if field in sample and not is_nonempty(sample.get(field)):
            violations.append(f"field must be non-empty: {field}")
    primary = contract.get("primary_field")
    if isinstance(primary, str) and not is_nonempty(sample.get(primary)):
        violations.append(f"primary output field must be non-empty: {primary}")
    if contract.get("json_serializable") is True:
        try:
            json.dumps(sample)
        except TypeError as exc:
            violations.append(f"sample output is not JSON serializable: {exc}")
    if TASK_TYPE.lower().replace("-", "_") == "vad":
        segments = sample.get("speech_segments")
        if not isinstance(segments, list) or not segments:
            violations.append("VAD output requires non-empty speech_segments")
        elif vad_duration is None:
            violations.append("VAD contract validation requires fixture duration")
        else:
            violations.extend(
                validate_vad_intervals(
                    segments,
                    field="speech_segments",
                    duration=vad_duration,
                    required_fields=("start", "end"),
                )
            )
        frame_scores = sample.get("frame_scores")
        if frame_scores is not None and vad_duration is not None:
            violations.extend(
                validate_vad_intervals(
                    frame_scores,
                    field="frame_scores",
                    duration=vad_duration,
                    required_fields=("start", "end", "score"),
                )
            )
    return violations


def stage_import() -> bool:
    started = time.time()
    try:
        import_wrapper_class()
    except Exception as exc:  # noqa: BLE001
        append_log("VALIDATE_IMPORT", "failed", str(exc))
        write_stage_result("import", False, started, str(exc))
        return False
    append_log("VALIDATE_IMPORT", "passed", "Wrapper import succeeded.")
    write_stage_result("import", True, started)
    return True


def stage_load() -> bool:
    started = time.time()
    try:
        load_wrapper()
    except Exception as exc:  # noqa: BLE001
        append_log("VALIDATE_LOAD", "failed", str(exc))
        write_stage_result("load", False, started, str(exc))
        return False
    append_log("VALIDATE_LOAD", "passed", "Wrapper load succeeded.")
    write_stage_result("load", True, started)
    return True


def stage_infer() -> bool:
    started = time.time()
    try:
        wrapper = load_wrapper()
        payload = first_fixture_payload()
        sample = run_predict(wrapper, payload)
        if not sample:
            raise AssertionError("prediction output is empty")
        write_json(SAMPLE_OUTPUT, sample)
    except Exception as exc:  # noqa: BLE001
        append_log("VALIDATE_INFER", "failed", str(exc))
        write_stage_result("infer", False, started, str(exc))
        return False
    append_log("VALIDATE_INFER", "passed", "Inference produced sample_output.json.")
    write_stage_result(
        "infer",
        True,
        started,
        output_summary=json.dumps(sample, ensure_ascii=True)[:500],
    )
    return True


def stage_contract() -> bool:
    started = time.time()
    try:
        if not SAMPLE_OUTPUT.exists():
            # infer and contract are coupled through SURE_VALIDATE_ARTIFACTS_DIR:
            # infer writes the sample there and contract reads it back. Pointing
            # the two stages at separate directories fails here every time, so say
            # so rather than reporting a bare missing file.
            seen = sorted(child.name for child in ARTIFACTS_DIR.iterdir()) if ARTIFACTS_DIR.is_dir() else []
            raise FileNotFoundError(
                f"Missing sample output: {SAMPLE_OUTPUT}. The infer stage writes "
                f"sample_output.json into SURE_VALIDATE_ARTIFACTS_DIR, so contract must run "
                f"with the same directory infer used. This one holds: {seen or 'nothing'}"
            )
        sample = json.loads(SAMPLE_OUTPUT.read_text(encoding="utf-8"))
        if not isinstance(sample, dict):
            raise ValueError("sample_output.json must be an object")
        contract = load_io_contract()
        duration = vad_fixture_duration() if TASK_TYPE.lower().replace("-", "_") == "vad" else None
        violations = validate_contract(sample, contract, duration)
        if violations:
            raise AssertionError("; ".join(violations))
    except Exception as exc:  # noqa: BLE001
        append_log("VALIDATE_CONTRACT", "failed", str(exc))
        write_stage_result(
            "contract",
            False,
            started,
            str(exc),
            io_contract_satisfied=False,
            violations=[str(exc)],
            io_contract=IO_CONTRACT,
        )
        return False
    append_log("VALIDATE_CONTRACT", "passed", "Sample output satisfies io_contract.")
    write_stage_result(
        "contract",
        True,
        started,
        io_contract_satisfied=True,
        violations=[],
        io_contract=contract,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["all", "import", "load", "infer", "contract"], default="all")
    args = parser.parse_args()

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stages = [args.stage] if args.stage != "all" else ["import", "load", "infer", "contract"]
    ok = True
    for stage in stages:
        if stage == "import":
            ok = stage_import() and ok
        elif stage == "load":
            ok = stage_load() and ok
        elif stage == "infer":
            ok = stage_infer() and ok
        elif stage == "contract":
            ok = stage_contract() and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
