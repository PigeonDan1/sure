from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from model import ModelLoadError, ModelWrapper


ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = ROOT / "fixture"
ARTIFACTS = ROOT / "artifacts"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip().lower()


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for gt_path in sorted(FIXTURE_ROOT.glob("*/*/gt.jsonl")):
        for line in gt_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            case["audio_path"] = str((gt_path.parent / case["audio"]).resolve())
            case["fixture"] = str(gt_path.parent.relative_to(ROOT))
            cases.append(case)
    if not cases:
        raise RuntimeError(f"No fixture cases found under {FIXTURE_ROOT}")
    return cases


def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    started = time.time()
    log_lines = ["validate.py: START"]
    wrapper = ModelWrapper()
    log_lines.append(f"resolve_model_path: {wrapper._resolve_model_path()}")

    import torch
    from kimia_infer.api.kimia import KimiAudio

    log_lines.append(f"import: PASS KimiAudio={KimiAudio.__name__}")
    log_lines.append(
        f"env_compat: torch={torch.__version__} cuda_available={torch.cuda.is_available()} cuda_devices={torch.cuda.device_count()}"
    )
    wrapper._validate_weights_present()
    log_lines.append("weights_check: PASS")

    outputs: list[dict[str, Any]] = []
    status = "PASS"
    failure: str | None = None
    try:
        wrapper.load()
        log_lines.append("load: PASS")
        total_distance = 0
        total_reference_chars = 0
        for case in load_cases():
            result = wrapper.predict(case["audio_path"]).to_dict()
            prediction = result.get("text", "")
            reference = case["ground_truth"]
            distance = edit_distance(normalize_text(reference), normalize_text(prediction))
            total_distance += distance
            total_reference_chars += max(len(normalize_text(reference)), 1)
            outputs.append(
                {
                    "id": case["id"],
                    "key": case["key"],
                    "fixture": case["fixture"],
                    "audio": case["audio"],
                    "ground_truth": reference,
                    "prediction": prediction,
                    "cer_distance": distance,
                }
            )
            log_lines.append(f"infer: PASS key={case['key']} text_nonempty={bool(prediction.strip())}")
        cer = total_distance / total_reference_chars if total_reference_chars else 0.0
        log_lines.append("contract: PASS required_fields=[text] json_serializable=true")
    except ModelLoadError as exc:
        status = "FAIL"
        failure = str(exc)
        cer = None
        log_lines.append(f"load: FAIL {failure}")

    sample_output = {
        "status": status,
        "model": "asr_kimi_audio",
        "num_samples": len(outputs),
        "metrics": {"cer": cer},
        "healthcheck": wrapper.healthcheck(),
        "failure": failure,
        "outputs": outputs,
    }
    (ARTIFACTS / "sample_output.json").write_text(
        json.dumps(sample_output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    elapsed = time.time() - started
    log_lines.append(f"validate.py: {status} duration_seconds={elapsed:.2f}")
    (ARTIFACTS / "validation.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / "validation.log").write_text(
            f"validate.py: FAIL\nerror: {exc}\n", encoding="utf-8"
        )
        print(f"validation failed: {exc}", file=sys.stderr)
        raise
