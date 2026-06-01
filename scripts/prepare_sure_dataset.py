#!/usr/bin/env python3
"""
Prepare canonical SURE-EVAL datasets deterministically.

This script is intentionally boring: it only downloads / converts datasets and
emits a machine-readable summary. It is meant to reduce agent-side uncertainty.
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


def prepare_dataset(manager: DatasetManager, dataset_name: str) -> dict[str, Any]:
    """Prepare a single dataset and return a summary."""
    canonical_name = manager.normalize_dataset_name(dataset_name)
    jsonl_path = manager.download_and_convert(dataset_name)
    info = manager.get_info(canonical_name) or {}

    return {
        "dataset": canonical_name,
        "requested_name": dataset_name,
        "jsonl_path": str(jsonl_path),
        "task": info.get("task"),
        "language": info.get("language"),
        "source": info.get("source"),
        "display_name": info.get("display_name"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare deterministic SURE-EVAL datasets")
    parser.add_argument("--dataset", nargs="+", help="Dataset names to prepare")
    parser.add_argument("--all", action="store_true", help="Prepare all configured datasets")
    parser.add_argument("--config", type=str, help="Config path")
    parser.add_argument("--output", type=str, help="Optional JSON summary output path")
    args = parser.parse_args()

    if not args.dataset and not args.all:
        parser.error("Specify --dataset ... or --all")

    cfg = Config.from_yaml(args.config) if args.config else Config.from_env()
    manager = DatasetManager(cfg)

    dataset_names = args.dataset or list(cfg.datasets.definitions.keys())
    prepared: list[dict[str, Any]] = []

    for dataset_name in dataset_names:
        logger.info("Preparing dataset", dataset=dataset_name)
        prepared.append(prepare_dataset(manager, dataset_name))

    summary = {"prepared": prepared}
    output = json.dumps(summary, indent=2, ensure_ascii=False)
    print(output)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        logger.info("Wrote preparation summary", path=str(output_path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
