#!/usr/bin/env python3
"""
Validate deterministic prediction files before formal evaluation.

Checks:
- dataset resolves to a canonical JSONL
- prediction file exists
- all expected keys are present
- optionally require non-empty predictions
- report missing / extra / duplicate keys in a machine-readable way
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.config import Config
from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.datasets import DatasetManager

configure_logging(level="INFO")
logger = get_logger(__name__)
REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    samples = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))
    return samples


def _load_predictions(path: Path) -> tuple[dict[str, str], list[str]]:
    predictions: dict[str, str] = {}
    duplicate_keys: list[str] = []

    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not line.strip():
                continue
            if "\t" in line:
                key, prediction = line.split("\t", 1)
            else:
                parts = line.split(None, 1)
                key = parts[0]
                prediction = parts[1] if len(parts) > 1 else ""

            if key in predictions:
                duplicate_keys.append(key)
            predictions[key] = prediction

    return predictions, duplicate_keys


def _load_structured_predictions(path: Path) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    predictions: dict[str, dict[str, Any]] = {}
    duplicate_keys: list[str] = []
    invalid_rows: list[str] = []
    if not path.exists():
        return predictions, duplicate_keys, invalid_rows
    with path.open("r", encoding="utf-8") as handle:
        for index, raw_line in enumerate(handle, 1):
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                invalid_rows.append(f"line:{index}")
                continue
            key = str(row.get("key", ""))
            if not key:
                invalid_rows.append(f"line:{index}:missing_key")
                continue
            if key in predictions:
                duplicate_keys.append(key)
            predictions[key] = row
    return predictions, duplicate_keys, invalid_rows


def _resolve_audio_path(value: Any, base_dir: Path | None) -> Path:
    path = Path(str(value))
    if path.is_absolute() and not path.exists():
        workspace_root = Path("/workspace/sure-eval")
        try:
            relative_to_workspace = path.relative_to(workspace_root)
        except ValueError:
            pass
        else:
            remapped = REPO_ROOT / relative_to_workspace
            if remapped.exists():
                path = remapped
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return path


def _task_contract_violations(
    samples: list[dict[str, Any]],
    structured: dict[str, dict[str, Any]],
    *,
    base_dir: Path | None = None,
) -> list[str]:
    violations: list[str] = []
    sample_by_key = {str(sample.get("key", "")): sample for sample in samples}
    for key, row in structured.items():
        sample = sample_by_key.get(key, {})
        task = str(row.get("task") or sample.get("task") or "").upper()
        prediction = row.get("prediction") if isinstance(row.get("prediction"), dict) else {}
        normalized = str(row.get("normalized_prediction") or "")
        if task in {"ASR", "S2TT"} and not (prediction.get("text") or normalized):
            violations.append(key)
        elif task in {"TTS", "VC"}:
            audio_path = prediction.get("audio_path") or normalized
            if not audio_path or not _resolve_audio_path(audio_path, base_dir).exists():
                violations.append(key)
        elif task in {"SER", "GR"} and not (prediction.get("label") or normalized):
            violations.append(key)
        elif task == "SLU" and not (prediction.get("text") or prediction.get("label") or normalized):
            violations.append(key)
        elif task in {"SD", "SA-ASR"} and not (prediction.get("segments") or prediction.get("annotation") or normalized):
            violations.append(key)
        elif task == "KWS" and not (prediction.get("score") is not None or prediction.get("events") or normalized):
            violations.append(key)
    return sorted(set(violations))


def _structured_projection_mismatches(
    predictions: dict[str, str],
    structured: dict[str, dict[str, Any]],
    expected_keys: set[str],
) -> list[str]:
    mismatches: list[str] = []
    for key in sorted(expected_keys & set(predictions) & set(structured)):
        normalized = str(structured[key].get("normalized_prediction") or "")
        if predictions[key] != normalized:
            mismatches.append(key)
    return mismatches


def validate_prediction_file(
    dataset_manager: DatasetManager,
    dataset_name: str,
    prediction_path: Path,
    require_nonempty: bool,
    max_samples: int = 0,
) -> dict[str, Any]:
    canonical_name = dataset_manager.normalize_dataset_name(dataset_name)
    jsonl_path = dataset_manager.get_jsonl_path(canonical_name)
    if not jsonl_path.exists():
        jsonl_path = dataset_manager.download_and_convert(canonical_name)

    all_samples = _load_jsonl(jsonl_path)
    samples = all_samples[:max_samples] if max_samples > 0 else all_samples
    predictions, duplicate_keys = _load_predictions(prediction_path)
    structured_path = prediction_path.with_suffix(".jsonl")
    structured_predictions, structured_duplicate_keys, invalid_structured_rows = _load_structured_predictions(structured_path)

    expected_keys = [str(sample.get("key", "")) for sample in samples]
    expected_key_set = set(expected_keys)
    prediction_key_set = set(predictions.keys())

    missing_keys = sorted(expected_key_set - prediction_key_set)
    extra_keys = sorted(prediction_key_set - expected_key_set)
    empty_prediction_keys = sorted(
        key for key, prediction in predictions.items() if key in expected_key_set and prediction.strip() == ""
    )
    structured_key_set = set(structured_predictions.keys())
    structured_missing_keys = sorted(expected_key_set - structured_key_set) if structured_path.exists() else []
    structured_extra_keys = sorted(structured_key_set - expected_key_set) if structured_path.exists() else []
    contract_violation_keys = (
        _task_contract_violations(samples, structured_predictions, base_dir=structured_path.parent)
        if structured_path.exists()
        else []
    )
    structured_projection_mismatch_keys = (
        _structured_projection_mismatches(predictions, structured_predictions, expected_key_set)
        if structured_path.exists()
        else []
    )

    is_valid = not missing_keys and not extra_keys and not duplicate_keys
    if require_nonempty and empty_prediction_keys:
        is_valid = False
    if structured_path.exists() and (
        structured_missing_keys
        or structured_extra_keys
        or structured_duplicate_keys
        or invalid_structured_rows
        or contract_violation_keys
        or structured_projection_mismatch_keys
    ):
        is_valid = False

    return {
        "dataset": canonical_name,
        "jsonl_path": str(jsonl_path),
        "prediction_path": str(prediction_path),
        "prediction_jsonl_path": str(structured_path) if structured_path.exists() else None,
        "format_used": "jsonl+txt" if structured_path.exists() else "txt",
        "total_dataset_samples": len(all_samples),
        "max_samples": max_samples if max_samples > 0 else None,
        "expected_samples": len(expected_keys),
        "provided_predictions": len(predictions),
        "missing_keys": missing_keys,
        "extra_keys": extra_keys,
        "duplicate_keys": sorted(set(duplicate_keys)),
        "empty_prediction_keys": empty_prediction_keys,
        "structured_missing_keys": structured_missing_keys,
        "structured_extra_keys": structured_extra_keys,
        "structured_duplicate_keys": sorted(set(structured_duplicate_keys)),
        "invalid_structured_rows": invalid_structured_rows,
        "structured_projection_mismatch_keys": structured_projection_mismatch_keys,
        "contract_violation_keys": contract_violation_keys,
        "require_nonempty": require_nonempty,
        "is_valid": is_valid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate deterministic prediction files")
    parser.add_argument("--dataset", nargs="+", required=True, help="Dataset names to validate")
    parser.add_argument("--pred-dir", type=str, help="Directory containing <dataset>.txt prediction files")
    parser.add_argument("--pred", action="append", nargs=2, metavar=("DATASET", "FILE"), help="Explicit dataset-to-prediction mapping")
    parser.add_argument("--config", type=str, help="Config path")
    parser.add_argument("--require-nonempty", action="store_true", help="Fail when any expected prediction is empty")
    parser.add_argument("--max-samples", type=int, default=0, help="Validate only the first N dataset samples when running a bounded evaluation")
    parser.add_argument("--output", type=str, help="Optional JSON output path")
    args = parser.parse_args()

    cfg = Config.from_yaml(args.config) if args.config else Config.from_env()
    dataset_manager = DatasetManager(cfg)

    explicit_preds = {dataset_manager.normalize_dataset_name(name): Path(path) for name, path in (args.pred or [])}
    pred_dir = Path(args.pred_dir) if args.pred_dir else None

    results: list[dict[str, Any]] = []
    overall_valid = True

    for requested_dataset in dataset_manager.expand_dataset_names(args.dataset):
        canonical_name = dataset_manager.normalize_dataset_name(requested_dataset)
        prediction_path = explicit_preds.get(canonical_name)
        if prediction_path is None:
            if pred_dir is None:
                raise ValueError(f"No prediction file provided for dataset: {canonical_name}")
            prediction_path = pred_dir / f"{canonical_name}.txt"
        if not prediction_path.exists():
            raise FileNotFoundError(f"Prediction file not found: {prediction_path}")

        result = validate_prediction_file(
            dataset_manager=dataset_manager,
            dataset_name=canonical_name,
            prediction_path=prediction_path,
            require_nonempty=args.require_nonempty,
            max_samples=max(0, args.max_samples),
        )
        overall_valid = overall_valid and result["is_valid"]
        results.append(result)

    payload = {"is_valid": overall_valid, "results": results}
    output = json.dumps(payload, indent=2, ensure_ascii=False)
    print(output)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        logger.info("Wrote validation payload", path=str(output_path))

    return 0 if overall_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
