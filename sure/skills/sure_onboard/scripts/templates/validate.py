#!/usr/bin/env python3
"""Model-local validation template for SURE /sure_onboard.

The generated model directory should customize the constants below and keep the
CLI contract stable:

    python validate.py --stage import
    python validate.py --stage load
    python validate.py --stage infer
    python validate.py --stage contract
    python validate.py --stage all

Each stage writes artifacts/<stage>_result.json. Inference writes the first
result to artifacts/sample_output.json for contract compatibility and all
fixture results to artifacts/sample_outputs.jsonl.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = MODEL_DIR / "artifacts"
VALIDATION_LOG = ARTIFACTS_DIR / "validation.log"
SAMPLE_OUTPUT = ARTIFACTS_DIR / "sample_output.json"
SAMPLE_OUTPUTS = ARTIFACTS_DIR / "sample_outputs.jsonl"

# Agent-filled constants.
MODEL_ID = "__MODEL_ID__"
TASK_TYPE = "__TASK_TYPE__"
WRAPPER_MODULE = "model"
WRAPPER_CLASS = "__WRAPPER_CLASS__"
PREDICT_METHOD = "__PREDICT_METHOD__"
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
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


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
    module = importlib.import_module(WRAPPER_MODULE)
    return getattr(module, WRAPPER_CLASS)


def instantiate_wrapper() -> Any:
    wrapper_cls = import_wrapper_class()
    model_path = os.environ.get("MODEL_PATH", MODEL_ID)
    device = os.environ.get("DEVICE", os.environ.get("SURE_DEVICE", "auto"))
    attempts = [
        lambda: wrapper_cls(model_path=model_path, device=device),
        lambda: wrapper_cls({"model_path": model_path, "device": device}),
        lambda: wrapper_cls(),
    ]
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("failed to instantiate wrapper")


def load_wrapper() -> Any:
    wrapper = instantiate_wrapper()
    if hasattr(wrapper, "load"):
        wrapper.load()
    return wrapper


def fixture_payloads() -> list[dict[str, Any]]:
    raw_payload = os.environ.get("SURE_VALIDATE_INPUT_JSON")
    if raw_payload:
        parsed = json.loads(raw_payload)
        if not isinstance(parsed, dict):
            raise ValueError("SURE_VALIDATE_INPUT_JSON must decode to an object.")
        return [{"input": parsed, "fixture": {}}]

    fixture_root = MODEL_DIR / "fixture"
    for gt_path in sorted(fixture_root.glob("**/gt.jsonl")):
        payloads: list[dict[str, Any]] = []
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
                payloads.append(
                    {
                        "input": payload,
                        "fixture": {
                            "key": item.get("key"),
                            "audio": item.get("audio"),
                            "language": item.get("language"),
                            "dataset": item.get("dataset"),
                            "ground_truth": item.get("ground_truth"),
                            "duration": item.get("duration"),
                            "speech_segments": item.get("speech_segments"),
                        },
                    }
                )
        if payloads:
            if len(payloads) > 5:
                raise ValueError(f"Fixture set exceeds the 5-sample validation limit: {gt_path}")
            return payloads
    raise FileNotFoundError(
        "No validation payload found. Set SURE_VALIDATE_INPUT_JSON or provide fixture/**/gt.jsonl."
    )


def output_summary(outputs: list[dict[str, Any]]) -> str:
    first = outputs[0]
    summarized: dict[str, Any] = {}
    for key, value in first.items():
        if isinstance(value, str):
            summarized[key] = value[:200]
        elif isinstance(value, (int, float, bool)) or value is None:
            summarized[key] = value
        else:
            summarized[key] = {"type": type(value).__name__}
    return json.dumps(
        {"sample_count": len(outputs), "first_output": summarized},
        ensure_ascii=False,
    )


def to_plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return to_plain(value.to_dict())
    if dataclasses.is_dataclass(value):
        return to_plain(dataclasses.asdict(value))
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
    predict = getattr(wrapper, PREDICT_METHOD, None) or getattr(wrapper, "predict", None)
    if predict is None:
        raise AttributeError(f"Wrapper has neither {PREDICT_METHOD!r} nor 'predict'.")
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
    spec_path = MODEL_DIR / "model.spec.yaml"
    if not spec_path.exists():
        raise FileNotFoundError("model.spec.yaml is required when IO_CONTRACT is not filled.")
    try:
        import yaml
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("PyYAML is required to read model.spec.yaml io_contract.") from exc
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or not isinstance(spec.get("io_contract"), dict):
        raise ValueError("model.spec.yaml must contain io_contract.")
    return spec["io_contract"]


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
    payloads = fixture_payloads()
    value = payloads[0].get("fixture", {}).get("duration")
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
    required = string_list(contract.get("required_fields"))
    nonempty = string_list(contract.get("nonempty_fields"))
    primary = contract.get("primary_field")
    if isinstance(primary, str) and primary:
        if primary not in required:
            required.append(primary)
        if primary not in nonempty:
            nonempty.append(primary)
    for field in required:
        if field not in sample:
            violations.append(f"required field missing: {field}")
    for field in nonempty:
        if field in sample and not is_nonempty(sample[field]):
            violations.append(f"field must be nonempty: {field}")
    if contract.get("output_type") == "audio" and not any(
        key in sample for key in ("audio_path", "wavs", "wavs_summary", "sample_rate")
    ):
        violations.append("audio output requires audio_path, wavs, wavs_summary, or sample_rate evidence")
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
        payloads = fixture_payloads()
        outputs: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        for index, fixture in enumerate(payloads, start=1):
            payload = fixture["input"]
            sample = run_predict(wrapper, payload)
            if not sample:
                raise AssertionError(f"prediction output is empty for fixture {index}")
            outputs.append(sample)
            rows.append(
                {
                    "id": index,
                    "key": fixture["fixture"].get("key"),
                    "audio": fixture["fixture"].get("audio"),
                    "language": fixture["fixture"].get("language") or payload.get("language"),
                    "dataset": fixture["fixture"].get("dataset"),
                    "ground_truth": fixture["fixture"].get("ground_truth"),
                    "output": sample,
                }
            )
        write_json(SAMPLE_OUTPUT, outputs[0])
        write_jsonl(SAMPLE_OUTPUTS, rows)
    except Exception as exc:  # noqa: BLE001
        append_log("VALIDATE_INFER", "failed", str(exc))
        write_stage_result("infer", False, started, str(exc))
        return False
    append_log("VALIDATE_INFER", "passed", f"Inference passed for {len(outputs)} fixture sample(s).")
    write_stage_result(
        "infer",
        True,
        started,
        output_summary=output_summary(outputs),
        sample_outputs_path="artifacts/sample_outputs.jsonl",
    )
    return True


def stage_contract() -> bool:
    started = time.time()
    try:
        if not SAMPLE_OUTPUT.exists():
            raise FileNotFoundError(f"Missing sample output: {SAMPLE_OUTPUT}")
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
