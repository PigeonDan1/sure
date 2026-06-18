"""Pre-SURE Screening Report generation.

Report1 sits after Paper_to_UserSpec and before SURE onboarding. It summarizes
paper-derived evidence, draft quality, optional external audit state, and SURE
fit without changing paper-only confidence semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io import read_json, read_text, read_yaml, write_json, write_text
from .schema import METRIC_COMPATIBILITY, TASK_IO_COMPATIBILITY, TASK_TYPES

REPORT1_VERSION = "report1_v1"
AUDIT_CHECKS = [
    "github_repo_exists",
    "repo_accessible",
    "readme_exists",
    "requirements_or_env_exists",
    "license_exists",
    "checkpoint_or_weights_link_exists",
    "model_card_exists",
    "example_inference_exists",
    "recent_activity",
]


def generate_report1(
    *,
    input_dir: str | Path,
    output_dir: str | Path,
    enable_external_audit: bool = False,
    offline: bool = True,
    external_evidence_json: str | Path | None = None,
    debug_artifacts: bool = False,
) -> dict[str, Any]:
    del debug_artifacts
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    external_items = _read_external_items(external_evidence_json)

    # --- Gate: Report1 requires external audit ---
    existing_audit = _read_optional_json(output_path / "report1_external_resource_audit.json")
    has_preexisting_checked_audit = existing_audit.get("status") == "checked"
    will_run_checked_audit = enable_external_audit and not offline

    if not will_run_checked_audit and not has_preexisting_checked_audit and not external_items:
        return {
            "audit_required": True,
            "message": "Report1 requires external audit. Run with --enable-external-audit --online or provide completed external audit results.",
        }
    # ---------------------------------------------

    user_spec = read_json(input_path / "user_spec_query.json")
    model_input = _read_optional_yaml(input_path / "MODEL_INPUT.yaml")
    validation = _read_optional_json(input_path / "validation_report.json")
    confidence = read_json(input_path / "paper_confidence_report.json")
    paper_text = read_text(input_path / "canonical_paper.md")
    evidence_cards = _read_jsonl(input_path / "paper_evidence_cards.jsonl")

    evidence_by_field = _cards_by_field(evidence_cards)
    trace: list[dict[str, Any]] = []
    identity = _paper_identity(user_spec)
    evidence_summary = _paper_evidence_summary(user_spec, evidence_by_field, confidence, trace)
    draft_review = _sure_draft_review(user_spec, model_input, validation)
    confidence_summary = _paper_confidence_summary(confidence)

    if has_preexisting_checked_audit and not enable_external_audit and not external_items:
        external_audit = existing_audit
    else:
        external_audit = _external_resource_audit(
            user_spec=user_spec,
            enable_external_audit=enable_external_audit,
            offline=offline,
            external_items=external_items,
            output_dir=output_path,
            trace=trace,
        )
        # Treat manually-provided external evidence as a completed audit.
        if external_items and not enable_external_audit:
            external_audit["status"] = "checked"
            external_audit["mode"] = "external_evidence_json"

    sure_fit = _sure_fit_analysis(user_spec, model_input, evidence_summary, draft_review)
    scores = _scores(confidence_summary, external_audit, sure_fit, draft_review, evidence_summary, external_items)
    decision = _decision(scores, confidence_summary, external_audit, sure_fit, draft_review, evidence_summary)
    limitations = _limitations(confidence_summary, external_audit, evidence_summary, draft_review)
    generated = [
        "report1_screening_report.md",
        "report1_screening_report.json",
        "report1_evidence_trace.jsonl",
        "report1_external_resource_audit.json",
    ]
    report = {
        "report_type": "pre_sure_screening_report",
        "report_version": REPORT1_VERSION,
        "scope": "paper_plus_optional_external_audit",
        "paper_identity": identity,
        "paper_evidence_summary": evidence_summary,
        "sure_draft_review": draft_review,
        "paper_confidence_summary": confidence_summary,
        "external_resource_audit": external_audit,
        "sure_fit_analysis": sure_fit,
        "scores": scores,
        "decision": decision,
        "limitations": limitations,
        "generated_artifacts": generated,
    }
    write_json(output_path / "report1_screening_report.json", report)
    write_json(output_path / "report1_external_resource_audit.json", external_audit)
    _write_jsonl(output_path / "report1_evidence_trace.jsonl", trace)
    write_text(output_path / "report1_screening_report.md", _markdown(report))
    return report


def _paper_identity(user_spec: dict[str, Any]) -> dict[str, Any]:
    source = user_spec.get("source", {})
    data = user_spec.get("data", {})
    return {
        "paper_title": source.get("paper_title"),
        "model_name": user_spec.get("model", {}).get("name"),
        "primary_task": user_spec.get("task", {}).get("primary_task"),
        "repo_url": source.get("repo_url"),
        "model_card_url": source.get("model_card_url"),
        "claimed_datasets": _unique([*data.get("eval_datasets", []), *data.get("downstream_datasets", [])]),
        "claimed_metrics": user_spec.get("evaluation", {}).get("metrics", []),
    }


def _paper_evidence_summary(
    user_spec: dict[str, Any],
    evidence_by_field: dict[str, list[dict[str, Any]]],
    confidence: dict[str, Any],
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    field_map = {
        "model_evidence": ["model.name"],
        "task_evidence": ["task.primary_task"],
        "dataset_evidence": ["data.eval_datasets", "data.downstream_datasets"],
        "metric_evidence": ["evaluation.metrics"],
        "repo_evidence": ["source.repo_url"],
    }
    summary = {name: _evidence_entry(name, fields, evidence_by_field, trace) for name, fields in field_map.items()}
    availability = confidence.get("score_breakdown", {}).get("declared_availability_score", {}).get("criteria", {})
    summary["declared_availability_evidence"] = {
        "criteria": availability,
        "evidence_card_ids": _availability_card_ids(evidence_by_field),
        "interpretation": (
            "Declared availability is paper-derived only; it is not external verification. "
            "A zero score means missing or unparsed declaration, not proof that resources do not exist."
        ),
    }
    if summary["declared_availability_evidence"]["evidence_card_ids"]:
        trace.append({
            "conclusion_id": "declared_availability",
            "claim": "The paper contains availability-related evidence.",
            "source_type": "paper_evidence_card",
            "evidence_card_ids": summary["declared_availability_evidence"]["evidence_card_ids"],
            "confidence": "medium",
        })
    summary["dataset_ambiguity"] = (
        not user_spec.get("data", {}).get("eval_datasets")
        and bool(user_spec.get("data", {}).get("downstream_datasets"))
    )
    return summary


def _sure_draft_review(
    user_spec: dict[str, Any],
    model_input: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "user_spec_complete": not user_spec.get("missing_fields"),
        "model_input_complete": bool(model_input) and not validation.get("model_input_validation", {}).get("blocking_errors"),
        "missing_fields": user_spec.get("missing_fields", []),
        "ambiguous_fields": _ambiguous_fields(user_spec),
        "field_conflicts": user_spec.get("conflict_fields", []),
        "validation_status": validation.get("status", "unknown"),
        "validation_warnings": validation.get("warnings", []),
    }


def _paper_confidence_summary(confidence: dict[str, Any]) -> dict[str, Any]:
    breakdown = confidence.get("score_breakdown", {})
    field_score = _sum_points(breakdown.get("paper_field_evidence", {}))
    section_score = _sum_points(breakdown.get("section_coverage", {}))
    return {
        "paper_confidence_score": confidence.get("paper_confidence_score", confidence.get("overall_percent")),
        "decision_hint": confidence.get("decision_hint"),
        "scope": confidence.get("scope"),
        "scoring_version": confidence.get("scoring_version"),
        "human_review_required": confidence.get("human_review_required"),
        "field_evidence_score": field_score,
        "section_coverage_score": section_score,
        "declared_availability_score": confidence.get("declared_availability_score", 0),
        "warnings": confidence.get("warnings", []),
        "limitations": [cap.get("reason") for cap in confidence.get("caps_applied", []) if isinstance(cap, dict)],
    }


def _external_resource_audit(
    *,
    user_spec: dict[str, Any],
    enable_external_audit: bool,
    offline: bool,
    external_items: list[dict[str, Any]],
    output_dir: Path,
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    base = {
        "status": "not_checked",
        "mode": "offline_stub" if offline else "not_checked",
        "checks": {name: {"status": "not_checked", "evidence_ids": []} for name in AUDIT_CHECKS},
        "warnings": ["External resource audit was not enabled; Report1 uses paper evidence only."],
        "external_evidence_items_count": len(external_items),
    }
    if external_items:
        _apply_external_items(base, external_items, trace)
    if not enable_external_audit:
        return base
    if offline:
        base["warnings"] = ["External audit requested but offline mode is active; no network or clone was attempted."]
        return base

    from .external_evidence import collect_external_evidence

    result = collect_external_evidence(
        user_spec=user_spec,
        mode="auto",
        cache_dir=output_dir / "report1_external_cache",
        timeout_sec=20,
    )
    audit = {
        "status": "checked",
        "mode": "external_audit",
        "repo_summary": result.repo_summary,
        "model_card_summary": result.model_card_summary,
        "review_summary": result.review_summary,
        "warnings": result.warnings,
        "external_evidence_items_count": len(result.items),
        "checks": {name: {"status": "unknown", "evidence_ids": []} for name in AUDIT_CHECKS},
    }
    _apply_external_items(audit, result.items, trace)
    return audit


def _sure_fit_analysis(
    user_spec: dict[str, Any],
    model_input: dict[str, Any],
    evidence_summary: dict[str, Any],
    draft_review: dict[str, Any],
) -> dict[str, Any]:
    task = user_spec.get("task", {}).get("primary_task")
    metrics = set(user_spec.get("evaluation", {}).get("metrics", []) or [])
    allowed_metrics = METRIC_COMPATIBILITY.get(task, set())
    datasets = user_spec.get("data", {}).get("eval_datasets", []) or user_spec.get("data", {}).get("downstream_datasets", [])
    io_contract = model_input.get("io_contract", {}) if isinstance(model_input, dict) else {}
    task_rule = TASK_IO_COMPATIBILITY.get(task, {})
    return {
        "task_supported_by_sure": task in TASK_TYPES and task != "unknown",
        "input_type_match": io_contract.get("input_type") in task_rule.get("input_type", set()) if task_rule else False,
        "output_schema_match": io_contract.get("output_type") in task_rule.get("output_type", set()) if task_rule else False,
        "dataset_match": bool(datasets),
        "metric_match": bool(metrics & allowed_metrics) if allowed_metrics else bool(metrics),
        "fixture_feasibility": bool(model_input.get("fixture", {}).get("fallback_allowed")) if isinstance(model_input, dict) else False,
        "protocol_fit": draft_review["model_input_complete"] and not evidence_summary.get("dataset_ambiguity", False),
        "expected_onboarding_difficulty": _difficulty(user_spec, model_input, evidence_summary),
    }


def _scores(
    confidence: dict[str, Any],
    external_audit: dict[str, Any],
    sure_fit: dict[str, Any],
    draft_review: dict[str, Any],
    evidence_summary: dict[str, Any],
    external_items: list[dict[str, Any]],
) -> dict[str, Any]:
    paper_score = int(confidence.get("paper_confidence_score") or 0)
    paper_quality = round(paper_score * 0.30)
    resource_availability = _resource_points(external_audit, evidence_summary)
    implementation = _implementation_points(external_audit)
    risk = _risk_points(confidence, draft_review, evidence_summary)
    community = 5 if external_items else 0
    pre_sure = paper_quality + resource_availability + implementation + risk + community
    fit_parts = {
        "task_fit": 20 if sure_fit["task_supported_by_sure"] else 0,
        "io_fit": (10 if sure_fit["input_type_match"] else 0) + (10 if sure_fit["output_schema_match"] else 0),
        "dataset_fit": 20 if sure_fit["dataset_match"] else 8 if evidence_summary.get("dataset_ambiguity") else 0,
        "metric_fit": 20 if sure_fit["metric_match"] else 0,
        "onboarding_feasibility": 20 if sure_fit["fixture_feasibility"] else 8,
    }
    return {
        "pre_sure_screening_score": min(100, pre_sure),
        "sure_fit_score": min(100, sum(fit_parts.values())),
        "risk_level": "high" if pre_sure < 45 else "medium" if pre_sure < 70 else "low",
        "components": {
            "paper_evidence_quality": paper_quality,
            "resource_availability": resource_availability,
            "implementation_completeness": implementation,
            "reproducibility_risk": risk,
            "community_review_signal": community,
            "sure_fit": fit_parts,
        },
        "notes": ["paper_confidence_score contributes to Paper Evidence Quality; it is not a final reproducibility score."],
    }


def _decision(
    scores: dict[str, Any],
    confidence: dict[str, Any],
    external_audit: dict[str, Any],
    sure_fit: dict[str, Any],
    draft_review: dict[str, Any],
    evidence_summary: dict[str, Any],
) -> dict[str, Any]:
    pre = scores["pre_sure_screening_score"]
    fit = scores["sure_fit_score"]
    if pre >= 75 and fit >= 75 and external_audit.get("status") == "checked":
        label, meaning = "A", "Recommend onboarding"
    elif pre >= 60 and fit >= 65:
        label, meaning = "B", "Recommend onboarding with minor checks"
    elif pre >= 40 and fit >= 45:
        label, meaning = "C", "Manual review before onboarding"
    else:
        label, meaning = "D", "Do not onboard now"
    reasons = []
    if confidence.get("paper_confidence_score") is not None:
        reasons.append(f"Paper-derived: paper_confidence_score={confidence['paper_confidence_score']} supports draft quality only.")
    if external_audit.get("status") != "checked":
        reasons.append("External resource: repository/model-card resources were not verified in offline mode.")
    if evidence_summary.get("dataset_ambiguity"):
        reasons.append("Paper-derived: eval_datasets is empty while downstream_datasets is populated; dataset role needs confirmation.")
    if not draft_review["model_input_complete"]:
        reasons.append("SURE draft: MODEL_INPUT has missing or invalid fields.")
    if sure_fit["expected_onboarding_difficulty"] != "low":
        reasons.append(f"Heuristic: expected_onboarding_difficulty={sure_fit['expected_onboarding_difficulty']}.")
    return {
        "label": label,
        "meaning": meaning,
        "reasons": reasons,
        "human_review_required": label in {"C", "D"} or confidence.get("human_review_required") is True,
    }


def _markdown(report: dict[str, Any]) -> str:
    identity = report["paper_identity"]
    confidence = report["paper_confidence_summary"]
    scores = report["scores"]
    decision = report["decision"]
    lines = [
        "# Report1: Pre-SURE Screening Report",
        "",
        "## Paper Identity",
        f"- paper_title: {identity.get('paper_title')}",
        f"- model_name: {identity.get('model_name')}",
        f"- primary_task: {identity.get('primary_task')}",
        f"- repo_url: {identity.get('repo_url')}",
        f"- model_card_url: {identity.get('model_card_url')}",
        f"- claimed_datasets: {_join(identity.get('claimed_datasets'))}",
        f"- claimed_metrics: {_join(identity.get('claimed_metrics'))}",
        "",
        "## Paper Evidence Summary",
        _evidence_md(report["paper_evidence_summary"]),
        "",
        "## Extracted SURE Draft Review",
        _dict_md(report["sure_draft_review"]),
        "",
        "## Paper-only Confidence Summary",
        _dict_md(confidence),
        "",
        "## External Resource Audit",
        _dict_md(report["external_resource_audit"], skip={"checks"}),
        "",
        "## SURE Fit Analysis",
        _dict_md(report["sure_fit_analysis"]),
        "",
        "## Scores",
        f"- pre_sure_screening_score: {scores['pre_sure_screening_score']}",
        f"- sure_fit_score: {scores['sure_fit_score']}",
        f"- risk_level: {scores['risk_level']}",
        f"- note: {scores['notes'][0]}",
        "",
        "## Decision",
        f"- decision: {decision['label']} - {decision['meaning']}",
        f"- human_review_required: {decision['human_review_required']}",
        *[f"- reason: {reason}" for reason in decision["reasons"]],
        "",
        "## Limitations",
        *[f"- {item}" for item in report["limitations"]],
        "",
    ]
    return "\n".join(lines)


def _evidence_entry(
    conclusion_id: str,
    fields: list[str],
    evidence_by_field: dict[str, list[dict[str, Any]]],
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    cards = [card for field in fields for card in evidence_by_field.get(field, [])]
    ids = [card["id"] for card in cards if card.get("id")]
    if ids:
        trace.append({
            "conclusion_id": conclusion_id,
            "claim": f"The paper provides evidence for {', '.join(fields)}.",
            "source_type": "paper_evidence_card",
            "evidence_card_ids": ids,
            "confidence": "high",
        })
    return {"fields": fields, "evidence_card_ids": ids, "quotes": [card.get("evidence_text") for card in cards[:3]]}


def _apply_external_items(audit: dict[str, Any], items: list[dict[str, Any]], trace: list[dict[str, Any]]) -> None:
    kind_to_check = {
        "repo_access": "repo_accessible",
        "repo_readme": "readme_exists",
        "repo_dependency_file": "requirements_or_env_exists",
        "repo_license": "license_exists",
        "repo_checkpoint_declared": "checkpoint_or_weights_link_exists",
        "repo_weight_file_observed": "checkpoint_or_weights_link_exists",
        "model_card_access": "model_card_exists",
        "repo_inference_entrypoint": "example_inference_exists",
        "repo_inference_example": "example_inference_exists",
        "repo_commit": "recent_activity",
    }
    for item in items:
        check = kind_to_check.get(str(item.get("kind") or ""))
        if not check or check not in audit["checks"]:
            continue
        evidence_id = str(item.get("id") or f"ext_{check}")
        audit["checks"][check] = {"status": "observed", "evidence_ids": [evidence_id]}
        trace.append({
            "conclusion_id": check,
            "claim": f"External evidence suggests {check.replace('_', ' ')}.",
            "source_type": "external_resource_audit",
            "external_evidence_ids": [evidence_id],
            "confidence": "medium",
        })


def _limitations(confidence: dict[str, Any], audit: dict[str, Any], evidence: dict[str, Any], draft: dict[str, Any]) -> list[str]:
    items = ["Report1 is a screening report, not a final reproducibility score."]
    if audit.get("status") != "checked":
        items.append("External resources were not checked; repo completeness and weights availability require confirmation.")
    if confidence.get("declared_availability_score", 0) == 0:
        items.append("No declared availability was scored from paper text; this may be a parsing limitation or a missing declaration.")
    if evidence.get("dataset_ambiguity"):
        items.append("Dataset role is ambiguous because eval_datasets is empty while downstream_datasets is populated.")
    if draft.get("missing_fields"):
        items.append("Generated draft has missing fields that should be reviewed before onboarding.")
    return items


def _resource_points(audit: dict[str, Any], evidence: dict[str, Any]) -> int:
    if audit.get("status") == "checked":
        observed = sum(1 for check in audit.get("checks", {}).values() if check.get("status") == "observed")
        return min(25, round(observed / max(1, len(AUDIT_CHECKS)) * 25))
    points = 0
    if evidence.get("repo_evidence", {}).get("evidence_card_ids"):
        points += 7
    if evidence.get("declared_availability_evidence", {}).get("evidence_card_ids"):
        points += 3
    return points


def _implementation_points(audit: dict[str, Any]) -> int:
    if audit.get("status") != "checked":
        return 4
    checks = audit.get("checks", {})
    points = 0
    for name in ["readme_exists", "requirements_or_env_exists", "example_inference_exists", "checkpoint_or_weights_link_exists"]:
        if checks.get(name, {}).get("status") == "observed":
            points += 5
    return points


def _risk_points(confidence: dict[str, Any], draft: dict[str, Any], evidence: dict[str, Any]) -> int:
    points = 15
    if draft.get("missing_fields"):
        points -= 5
    if evidence.get("dataset_ambiguity"):
        points -= 3
    if confidence.get("declared_availability_score", 0) == 0:
        points -= 3
    if confidence.get("human_review_required"):
        points -= 3
    return max(0, points)


def _difficulty(user_spec: dict[str, Any], model_input: dict[str, Any], evidence: dict[str, Any]) -> str:
    if user_spec.get("missing_fields") or evidence.get("dataset_ambiguity"):
        return "high"
    if not model_input.get("repo", {}).get("url") or model_input.get("weights", {}).get("source") in {None, "unknown"}:
        return "medium"
    return "low"


def _read_optional_json(path: Path) -> dict[str, Any]:
    return read_json(path) if path.exists() else {}


def _read_optional_yaml(path: Path) -> dict[str, Any]:
    return read_yaml(path) if path.exists() else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _read_external_items(path: str | Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    payload = read_json(path)
    items = payload.get("items", [])
    return items if isinstance(items, list) else []


def _cards_by_field(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        result.setdefault(str(card.get("field") or "unknown"), []).append(card)
    return result


def _availability_card_ids(evidence_by_field: dict[str, list[dict[str, Any]]]) -> list[str]:
    fields = ["source.repo_url", "source.model_card_url", "model.checkpoint_source"]
    return [card["id"] for field in fields for card in evidence_by_field.get(field, []) if card.get("id")]


def _ambiguous_fields(user_spec: dict[str, Any]) -> list[str]:
    fields = []
    data = user_spec.get("data", {})
    if not data.get("eval_datasets") and data.get("downstream_datasets"):
        fields.append("data.eval_datasets vs data.downstream_datasets")
    fields.extend(user_spec.get("conflict_fields", []))
    return _unique(fields)


def _sum_points(breakdown: dict[str, Any]) -> int:
    return sum(int(item.get("points_awarded", 0) or 0) for item in breakdown.get("criteria", {}).values())


def _dict_md(data: dict[str, Any], *, skip: set[str] | None = None) -> str:
    skip = skip or set()
    return "\n".join(f"- {key}: {value}" for key, value in data.items() if key not in skip)


def _evidence_md(data: dict[str, Any]) -> str:
    lines = []
    for key, value in data.items():
        if key == "dataset_ambiguity":
            lines.append(f"- {key}: {value}")
        elif isinstance(value, dict):
            lines.append(f"- {key}: evidence_card_ids={value.get('evidence_card_ids', [])}")
    return "\n".join(lines)


def _join(items: Any) -> str:
    return ", ".join(str(item) for item in items or []) or "None recorded"


def _unique(items: list[Any]) -> list[Any]:
    return list(dict.fromkeys(items))
