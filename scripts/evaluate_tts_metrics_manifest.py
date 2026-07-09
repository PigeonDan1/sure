#!/usr/bin/env python3
"""Thin CLI for the external TTS metrics adapter."""

from __future__ import annotations

import argparse
from pathlib import Path

from sure_eval.evaluation.tts.metrics import evaluate_tts_metrics_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a TTS metrics JSONL manifest.")
    parser.add_argument("--manifest", required=True, help="Path to TTS metrics manifest JSONL.")
    parser.add_argument(
        "--metric-script",
        required=True,
        help="Path to the existing run_tts_metric_pipeline_docker.sh script.",
    )
    parser.add_argument("--cache-dir", required=True, help="Cache directory for metric models.")
    parser.add_argument("--work-root", required=True, help="Root directory for per-sample work dirs.")
    parser.add_argument("--output-summary", required=True, help="Path to summary JSON output.")
    parser.add_argument("--max-samples", type=int, default=None, help="Optional max samples to run.")
    parser.add_argument("--resume", action="store_true", help="Reuse existing per-sample merged.json.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed sample.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    evaluate_tts_metrics_manifest(
        manifest_path=Path(args.manifest),
        metric_script=Path(args.metric_script),
        cache_dir=Path(args.cache_dir),
        work_root=Path(args.work_root),
        output_summary=Path(args.output_summary),
        max_samples=args.max_samples,
        resume=args.resume,
        fail_fast=args.fail_fast,
    )


if __name__ == "__main__":
    main()
