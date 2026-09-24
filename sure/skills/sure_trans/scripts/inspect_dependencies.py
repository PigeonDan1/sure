#!/usr/bin/env python3
"""Write the dependency report for a transformation run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dependency_contract import (
    collect_dependency_evidence,
    dependency_status,
    evidence_digest,
    load_json,
    review_is_valid,
)


def review_roots(resolved: dict[str, Any]) -> list[Path]:
    return [
        Path(str(resolved["build_context"])).resolve(),
        Path(str(resolved["model_path"])).resolve(),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = load_json(artifacts / "trans_input_resolved.json")
    evidence = collect_dependency_evidence(resolved)
    digest = evidence_digest(evidence)
    output = artifacts / "inference_dependency_report.json"
    previous: dict[str, Any] = {}
    if output.is_file():
        try:
            previous = load_json(output)
        except (OSError, ValueError, json.JSONDecodeError):
            previous = {}
    agent_review = previous.get("agent_review") if previous.get("evidence_digest") == digest else None
    if agent_review is not None:
        valid, _ = review_is_valid(agent_review, evidence["review_signals"], review_roots(resolved))
        if not valid:
            agent_review = None

    status = dependency_status(evidence, agent_review)
    human_evidence = [
        f"entrypoint language: {evidence['entrypoint_language']}",
        f"syntax check: {evidence['syntax_check'].get('status', 'unknown')}",
        f"Python imports discovered: {len(evidence['python_imports'])}",
    ]
    human_evidence.extend(
        f"missing dependency: {item.get('path') or item.get('source') or item.get('resolved_path')}"
        for item in evidence["hard_conflicts"]
        if item.get("classification") == "missing" or item.get("kind") in {"syntax_error", "read_error"}
    )
    human_evidence.extend(
        f"external dependency: {item.get('path') or item.get('source') or item.get('resolved_path')}"
        for item in evidence["hard_conflicts"]
        if item.get("classification") == "external"
    )
    human_evidence.extend(
        f"dynamic reference requires Agent review: {item.get('file')}:{item.get('line')}"
        for item in evidence["review_signals"]
    )
    payload = {
        "schema": "sure.trans.dependencies.v2",
        **evidence,
        "review_required": bool(evidence["review_signals"]),
        "evidence_digest": digest,
        "agent_review": agent_review,
        "evidence": human_evidence,
        "status": status,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
