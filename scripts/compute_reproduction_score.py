#!/usr/bin/env python3
"""Compute a reproducible paper/local reproduction score for a run root."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SCORING_PATH = REPO_ROOT / "src" / "sure_eval" / "reproduction" / "scoring.py"

spec = importlib.util.spec_from_file_location("sure_eval_reproduction_scoring", SCORING_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load scoring module from {SCORING_PATH}")
scoring = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scoring
spec.loader.exec_module(scoring)

build_reproduction_score_markdown = scoring.build_reproduction_score_markdown
compute_reproduction_score = scoring.compute_reproduction_score
write_reproduction_score_outputs = scoring.write_reproduction_score_outputs


def _json_dict(value: str) -> dict[str, float]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("expected a JSON object")
    return parsed


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise argparse.ArgumentTypeError("expected a JSON array")
    return [str(item) for item in parsed]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--paper-weight", type=float, default=0.30)
    parser.add_argument("--local-weight", type=float, default=0.70)
    parser.add_argument("--runtime-weight", type=float, default=0.20)
    parser.add_argument("--metric-weight", type=float, default=0.80)
    parser.add_argument("--comparability-factor", type=float, default=None)
    parser.add_argument("--metric-weights-json", type=_json_dict, default=None)
    parser.add_argument("--dataset-weights-json", type=_json_dict, default=None)
    parser.add_argument("--exclude-metrics-json", type=_json_list, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = compute_reproduction_score(
            args.run_root,
            paper_weight=args.paper_weight,
            local_weight=args.local_weight,
            runtime_weight=args.runtime_weight,
            metric_weight=args.metric_weight,
            comparability_factor=args.comparability_factor,
            metric_weights=args.metric_weights_json,
            dataset_weights=args.dataset_weights_json,
            excluded_metrics=args.exclude_metrics_json,
            strict=args.strict,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        print(build_reproduction_score_markdown(report), file=sys.stderr)
        return 0 if report.status != "blocked" else 1

    write_reproduction_score_outputs(report, args.output_json, args.output_md)
    print(f"Wrote {args.output_json}")
    print(f"Wrote {args.output_md}")
    return 0 if report.status != "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
