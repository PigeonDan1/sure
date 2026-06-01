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


def validate_prediction_file(
    dataset_manager: DatasetManager,
    dataset_name: str,
    prediction_path: Path,
    require_nonempty: bool,
) -> dict[str, Any]:
    canonical_name = dataset_manager.normalize_dataset_name(dataset_name)
    jsonl_path = dataset_manager.get_jsonl_path(canonical_name)
    if not jsonl_path.exists():
        jsonl_path = dataset_manager.download_and_convert(canonical_name)

    samples = _load_jsonl(jsonl_path)
    predictions, duplicate_keys = _load_predictions(prediction_path)

    expected_keys = [sample.get("key", "") for sample in samples]
    expected_key_set = set(expected_keys)
    prediction_key_set = set(predictions.keys())

    missing_keys = sorted(expected_key_set - prediction_key_set)
    extra_keys = sorted(prediction_key_set - expected_key_set)
    empty_prediction_keys = sorted(
        key for key, prediction in predictions.items() if key in expected_key_set and prediction.strip() == ""
    )

    is_valid = not missing_keys and not extra_keys and not duplicate_keys
    if require_nonempty and empty_prediction_keys:
        is_valid = False

    return {
        "dataset": canonical_name,
        "jsonl_path": str(jsonl_path),
        "prediction_path": str(prediction_path),
        "expected_samples": len(expected_keys),
        "provided_predictions": len(predictions),
        "missing_keys": missing_keys,
        "extra_keys": extra_keys,
        "duplicate_keys": sorted(set(duplicate_keys)),
        "empty_prediction_keys": empty_prediction_keys,
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
    parser.add_argument("--output", type=str, help="Optional JSON output path")
    args = parser.parse_args()

    cfg = Config.from_yaml(args.config) if args.config else Config.from_env()
    dataset_manager = DatasetManager(cfg)

    explicit_preds = {dataset_manager.normalize_dataset_name(name): Path(path) for name, path in (args.pred or [])}
    pred_dir = Path(args.pred_dir) if args.pred_dir else None

    results: list[dict[str, Any]] = []
    overall_valid = True

    for requested_dataset in args.dataset:
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
