#!/usr/bin/env python3
"""Run the generic SURE paper-vs-local reproduction workflow.

This entrypoint is intentionally dry-run friendly. It can validate target
schemas and compare provided fixture/local-eval results without downloading
weights or running a full benchmark.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.reproduction.schema import LocalEval, PaperClaim
from sure_eval.reproduction.workflow import (
    build_dataset_readiness_report,
    build_model_readiness_report,
    compare_paper_and_local,
    metric_direction,
    write_json,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _paper_claims(config: dict[str, Any]) -> list[PaperClaim]:
    raw_claims = config.get("paper_claims")
    if raw_claims is None and config.get("paper_claim"):
        raw_claims = [config["paper_claim"]]
    if not raw_claims:
        raise ValueError("target config must contain paper_claims or paper_claim")

    claims = []
    for raw in raw_claims:
        payload = dict(raw)
        payload["metric_direction"] = metric_direction(
            str(payload.get("metric", "")),
            payload.get("metric_direction"),
        )
        claims.append(PaperClaim(**payload))
    return claims


def _local_evals(config: dict[str, Any]) -> list[LocalEval]:
    raw_items = config.get("local_eval")
    if raw_items is None:
        raw_items = config.get("local_evals", [])
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    return [LocalEval(**item) for item in raw_items]


def _local_key(item: LocalEval) -> tuple[str, str, str, str, str]:
    return (
        (item.model_name or "").lower(),
        (item.dataset or "").lower(),
        (item.split or "").lower(),
        (item.task or "").lower(),
        item.metric.lower(),
    )


def _claim_key(item: PaperClaim) -> tuple[str, str, str, str, str]:
    return (
        item.model_name.lower(),
        item.dataset.lower(),
        (item.split or "").lower(),
        item.task.lower(),
        item.metric.lower(),
    )


def run(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config = _load_json(config_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    claims = _paper_claims(config)
    write_json(output_dir / "paper_claims.json", [claim.to_dict() for claim in claims])

    model_reports = [
        build_model_readiness_report(claim.model_name)
        for claim in claims
    ]
    write_json(output_dir / "model_readiness_report.json", model_reports)

    dataset_targets = config.get("dataset_targets", {})
    dataset_reports = []
    for claim in claims:
        target = dataset_targets.get(claim.dataset, {})
        dataset_reports.append(
            build_dataset_readiness_report(
                dataset_name=claim.dataset,
                task=claim.task,
                split=claim.split,
                jsonl_path=target.get("jsonl_path"),
                dataset_dir=target.get("dataset_dir"),
                source_format=target.get("source_format", "unknown"),
            )
        )
    write_json(output_dir / "dataset_readiness_report.json", dataset_reports)

    local_evals = _local_evals(config)
    write_json(output_dir / "local_eval_result.json", [item.to_dict() for item in local_evals])

    local_by_key = {_local_key(item): item for item in local_evals}
    comparisons = [
        compare_paper_and_local(claim, local_by_key.get(_claim_key(claim))).to_dict()
        for claim in claims
    ]
    final = {
        "workflow_version": "generic_reproduction_v1",
        "steps": ["A_paper_target_extraction", "B_model_readiness", "C_dataset_readiness", "D_local_evaluation", "E_paper_vs_local_comparison"],
        "paper_claims": [claim.to_dict() for claim in claims],
        "model_readiness": model_reports,
        "dataset_readiness": dataset_reports,
        "local_eval": [item.to_dict() for item in local_evals],
        "comparison": comparisons,
    }
    write_json(output_dir / "final_reproduction_report.json", final)
    return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    run(args.target_config, args.output_dir)
    print(f"Wrote generic reproduction artifacts to {args.output_dir}")


if __name__ == "__main__":
    main()
