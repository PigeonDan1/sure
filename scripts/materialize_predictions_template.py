#!/usr/bin/env python3
"""
Materialize deterministic prediction templates for one or more datasets.

This script does not decide *which* datasets a model should run on.
It only turns canonical datasets into stable prediction placeholders plus a
machine-readable manifest, so later agent / human steps can fill them in.
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
from sure_eval.reports import SOTAManager

configure_logging(level="INFO")
logger = get_logger(__name__)


def _load_samples(jsonl_path: Path) -> list[dict[str, Any]]:
    """Load canonical dataset samples from JSONL."""
    samples = []
    with open(jsonl_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))
    return samples


def _materialize_one(
    dataset_manager: DatasetManager,
    sota_manager: SOTAManager,
    dataset_name: str,
    output_dir: Path,
    overwrite: bool,
) -> dict[str, Any]:
    """Create one prediction template file and its manifest entry."""
    canonical_name = dataset_manager.normalize_dataset_name(dataset_name)
    jsonl_path = dataset_manager.get_jsonl_path(canonical_name)
    if not jsonl_path.exists():
        jsonl_path = dataset_manager.download_and_convert(canonical_name)

    samples = _load_samples(jsonl_path)
    if not samples:
        raise ValueError(f"Dataset has no samples: {canonical_name}")

    output_dir.mkdir(parents=True, exist_ok=True)
    template_path = output_dir / f"{canonical_name}.txt"
    if template_path.exists() and not overwrite:
        raise FileExistsError(f"Template already exists: {template_path}")

    with open(template_path, "w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(f"{sample.get('key', '')}\t\n")

    info = dataset_manager.get_info(canonical_name) or {}
    metric = sota_manager.get_metric(canonical_name)

    return {
        "dataset": canonical_name,
        "display_name": info.get("display_name"),
        "task": samples[0].get("task", info.get("task")),
        "language": samples[0].get("language", info.get("language")),
        "metric": metric,
        "num_samples": len(samples),
        "jsonl_path": str(jsonl_path),
        "template_path": str(template_path),
        "format": "tsv:key<TAB>prediction",
        "source": info.get("source"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize deterministic prediction templates")
    parser.add_argument("--dataset", nargs="+", required=True, help="Dataset names to materialize")
    parser.add_argument("--output-dir", type=str, required=True, help="Directory to write prediction templates")
    parser.add_argument("--config", type=str, help="Config path")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing template files")
    parser.add_argument("--manifest-name", type=str, default="manifest.json", help="Manifest filename to write under output-dir")
    args = parser.parse_args()

    cfg = Config.from_yaml(args.config) if args.config else Config.from_env()
    dataset_manager = DatasetManager(cfg)
    sota_manager = SOTAManager()
    output_dir = Path(args.output_dir)

    templates: list[dict[str, Any]] = []
    for dataset_name in args.dataset:
        logger.info("Materializing prediction template", dataset=dataset_name)
        templates.append(
            _materialize_one(
                dataset_manager=dataset_manager,
                sota_manager=sota_manager,
                dataset_name=dataset_name,
                output_dir=output_dir,
                overwrite=args.overwrite,
            )
        )

    manifest = {
        "template_version": "1.0",
        "templates": templates,
    }
    manifest_path = output_dir / args.manifest_name
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote template manifest", path=str(manifest_path))
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
