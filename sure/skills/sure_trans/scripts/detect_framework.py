#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from framework_contract import collect_framework_evidence, deterministic_framework, evidence_digest


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def architecture_clarification(
    declared_model_framework: str,
    detected_model_framework: str,
    architecture_signals: list[str],
) -> str | None:
    matches = declared_model_framework == detected_model_framework or (
        declared_model_framework != "transformers" and detected_model_framework == "custom"
    )
    if declared_model_framework == "transformers" and detected_model_framework == "transformers" and matches:
        return None
    signals = ", ".join(architecture_signals) if architecture_signals else "no specific architecture family proven"
    if detected_model_framework == "custom":
        return (
            f"Declared model framework '{declared_model_framework}'. Static inspection found a custom PyTorch "
            f"implementation without a Transformers dependency; architecture signals: {signals}. The flow preserves "
            "this implementation and relies on original inference, adapter inference, and equivalence gates."
        )
    if detected_model_framework == "transformers":
        return (
            f"Declared model framework '{declared_model_framework}', while static inspection detected Transformers; "
            f"architecture signals: {signals}. The detected implementation is retained and validated by inference "
            "and equivalence gates."
        )
    return (
        f"Declared model framework '{declared_model_framework}', but static inspection could not determine a PyTorch "
        f"model implementation; architecture signals: {signals}."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    artifacts = run_dir / "artifacts"
    resolved = read_object(artifacts / "trans_input_resolved.json")
    dependencies = read_object(artifacts / "inference_dependency_report.json")
    evidence = collect_framework_evidence(resolved, dependencies)
    digest = evidence_digest(evidence)
    output = artifacts / "framework_detection.json"
    previous: dict = {}
    if output.is_file():
        try:
            previous = read_object(output)
        except (OSError, ValueError, json.JSONDecodeError):
            previous = {}
    agent_review = previous.get("agent_review") if previous.get("evidence_digest") == digest else None

    detected_framework = deterministic_framework(evidence)
    has_torch = bool(evidence["has_torch"])
    hard_conflicts = evidence["hard_conflicts"]
    review_signals = evidence["review_signals"]
    if detected_framework != "pytorch":
        status = "blocked"
    elif review_signals and not agent_review:
        status = "needs_review"
    elif isinstance(agent_review, dict) and agent_review.get("disposition") == "runtime_conflict":
        status = "blocked"
    else:
        status = "ready"

    declared_model_framework = str(resolved["model_framework"])
    detected_model_framework = "transformers" if evidence["has_transformers"] else "custom" if has_torch else "unknown"
    clarification = architecture_clarification(
        declared_model_framework,
        detected_model_framework,
        evidence["architecture_signals"],
    )
    model_framework_matches = declared_model_framework == detected_model_framework or (
        declared_model_framework != "transformers" and detected_model_framework == "custom"
    )
    human_evidence = []
    if has_torch:
        human_evidence.append("PyTorch import or dependency detected")
    if evidence["has_transformers"]:
        human_evidence.append("Transformers import or dependency detected")
    human_evidence.extend(
        f"hard incompatible framework evidence: {item['framework']} ({item.get('file', item.get('package', 'dependency'))})"
        for item in hard_conflicts
    )
    human_evidence.extend(
        f"review-only framework evidence: {item['framework']} ({item.get('file', item.get('package', 'dependency'))})"
        for item in review_signals
    )
    payload = {
        "schema": "sure.trans.framework_detection.v2",
        "declared_framework": resolved["framework"],
        "declared_model_framework": declared_model_framework,
        "detected_framework": detected_framework,
        "detected_model_framework": detected_model_framework,
        "framework_requirement_met": detected_framework == "pytorch" and status != "blocked",
        "model_framework_matches": model_framework_matches,
        "transformers_preferred": True,
        "clarification_required": clarification is not None,
        "architecture_signals": evidence["architecture_signals"],
        "architecture_clarification": clarification,
        "status": status,
        "evidence": human_evidence,
        "framework_evidence": evidence["framework_evidence"],
        "hard_conflicts": hard_conflicts,
        "review_signals": review_signals,
        "review_required": bool(review_signals),
        "evidence_digest": digest,
        "agent_review": agent_review,
        "auxiliary_runtimes_allowed": ["onnxruntime", "native_binary"],
        "scanned_python_files": evidence["scanned_python_files"],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
