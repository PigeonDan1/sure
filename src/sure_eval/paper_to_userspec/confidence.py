"""Paper-only confidence scoring for Paper_to_UserSpec."""

from __future__ import annotations

import re
from typing import Any

PAPER_CONFIDENCE_VERSION = "paper_confidence_v1"

PAPER_FIELD_WEIGHTS = {
    "model.name": 12, "task.primary_task": 12, "source.repo_url": 10,
    "source.model_card_url": 8, "evaluation.metrics": 10,
    "data.eval_datasets": 8, "data.downstream_datasets": 6,
}

SECTION_WEIGHTS = {"title": 5, "abstract": 8, "method": 8, "experiments": 8, "results": 8}


def spans_to_paper_evidence_cards(
    spans: list[dict[str, Any]],
    *,
    namespace: str = "paper_ev",
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for idx, span in enumerate(spans, start=1):
        if not isinstance(span, dict):
            continue
        field = str(span.get("field") or "unknown")
        source = str(span.get("source") or "paper_text")
        cards.append({
            "id": f"{namespace}_{idx:04d}", "card_namespace": namespace,
            "claim_id": f"claim_{_slug(field)}", "field": field,
            "claim_type": "paper_field" if span.get("candidate_status") != "rejected" else "candidate_field",
            "claim_text": f"{field} = {span.get('value')}", "evidence_text": span.get("quote") or "",
            "source_type": source, "source_name": "paper_text" if source == "paper_text" else source,
            "source_url": span.get("value") if field.endswith("_url") else None,
            "section_name": span.get("section_name"), "page_idx": span.get("page_idx"),
            "start_char": span.get("start_char"), "end_char": span.get("end_char"),
            "confidence": float(span.get("confidence") or 0.0),
            "candidate_status": span.get("candidate_status"), "usage_type": span.get("usage_type"),
            "quality_flags": span.get("quality_flags", []),
        })
    return cards


def compute_paper_confidence(
    user_spec: dict[str, Any],
    evidence_cards: list[dict[str, Any]],
    paper_parse_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del paper_parse_report
    cards = _normalize_cards(evidence_cards)
    paper_cards = [card for card in cards if card["source_type"] == "paper_text"]
    score, field_breakdown = _field_score(paper_cards, cards)
    section_score, section_breakdown = _section_score(paper_cards)
    availability_score, availability_breakdown = _availability_score(paper_cards)
    overall = min(100, score + section_score + availability_score)

    caps: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not paper_cards:
        overall = _cap(overall, 40, caps, "no_paper_evidence")
    substantive_sections = {card["section_name"] for card in paper_cards if card["section_name"] != "title"}
    if substantive_sections and substantive_sections <= {"abstract"}:
        overall = _cap(overall, 60, caps, "abstract_only_evidence")
    missing = _missing_critical_evidence(paper_cards, cards)
    if missing:
        overall = _cap(overall, 55, caps, "missing_critical_paper_evidence")
        warnings.append(f"Missing critical evidence for: {', '.join(missing)}")

    human_review_required = overall < 70 or bool(missing)
    decision = _decision_hint(overall, human_review_required)
    return {
        "overall": round(overall / 100, 4),
        "overall_percent": int(overall),
        "scoring_version": PAPER_CONFIDENCE_VERSION,
        "stage": "paper_confidence",
        "scope": "paper_only",
        "decision_hint": decision,
        "human_review_required": human_review_required,
        "paper_confidence_score": int(overall),
        "paper_evidence_score": int(score + section_score),
        "declared_availability_score": int(availability_score),
        "weighted_formula": "paper field evidence + paper section coverage + declared availability, with paper-only caps",
        "score_breakdown": {
            "paper_field_evidence": field_breakdown,
            "section_coverage": section_breakdown,
            "declared_availability_score": availability_breakdown,
        },
        "caps_applied": caps,
        "evidence_card_ids": [card["id"] for card in paper_cards if card.get("id")],
        "evidence_cards": paper_cards,
        "warnings": _unique(warnings),
    }


def confidence_for_user_spec(
    user_spec: dict[str, Any],
    evidence_cards: list[dict[str, Any]],
    paper_parse_report: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = compute_paper_confidence(user_spec, evidence_cards, paper_parse_report)
    old = user_spec.get("confidence", {}) if isinstance(user_spec.get("confidence"), dict) else {}
    confidence = {
        "overall": report["overall"],
        "overall_percent": report["overall_percent"],
        "extraction": old.get("extraction", "heuristic"),
        "scoring_version": report["scoring_version"],
        "paper_confidence_score": report["paper_confidence_score"],
        "declared_availability_score": report["declared_availability_score"],
        "paper_evidence_score": report["paper_evidence_score"],
        "decision_hint": report["decision_hint"],
        "human_review_required": report["human_review_required"],
        "training_recipe_indicated": bool(old.get("training_recipe_indicated", False)),
        "extraction_warnings": list(old.get("extraction_warnings", [])),
        "confidence_warnings": list(report.get("warnings", [])),
        "evidence_card_ids": list(report.get("evidence_card_ids", [])),
    }
    return confidence, report


def _normalize_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for idx, card in enumerate(cards, start=1):
        if not isinstance(card, dict) or card.get("candidate_status") == "rejected":
            continue
        source_type = str(card.get("source_type") or card.get("source") or "paper_text")
        if source_type not in {"paper_text", "user_provided"}:
            continue
        normalized.append({
            **card, "id": str(card.get("id") or f"paper_ev_{idx:04d}"),
            "field": str(card.get("field") or "unknown"), "source_type": source_type,
            "section_name": _section_name(card.get("section_name")),
            "evidence_text": str(card.get("evidence_text") or card.get("quote") or ""),
        })
    return normalized


def _field_score(paper_cards: list[dict[str, Any]], all_cards: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    criteria = {}
    total = 0
    for field, weight in PAPER_FIELD_WEIGHTS.items():
        pool = paper_cards
        if field == "source.repo_url":
            pool = [card for card in all_cards if card["source_type"] in {"paper_text", "user_provided"}]
        supporting = next((card for card in pool if card["field"] == field), None)
        points = weight if supporting else 0
        if supporting and supporting["source_type"] == "user_provided":
            points = min(points, round(weight * 0.6))
        total += points
        criteria[field] = {
            "points_awarded": points, "max_points": weight,
            "evidence_card_id": supporting.get("id") if supporting else None,
            "reason": f"supported by {supporting['id']}" if supporting else "no paper evidence",
        }
    return total, {"criteria": criteria}


def _section_score(cards: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    seen = {card["section_name"] for card in cards}
    criteria = {
        section: {"points_awarded": weight if section in seen else 0, "max_points": weight, "covered": section in seen}
        for section, weight in SECTION_WEIGHTS.items()
    }
    return sum(item["points_awarded"] for item in criteria.values()), {"criteria": criteria}


def _availability_score(cards: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    joined = "\n".join(card["evidence_text"] for card in cards).lower()
    rules = {
        "declared_code_available": (8, _has(joined, "github", "code", "repo", "repository") and _has(joined, "available", "released")),
        "declared_checkpoint_available": (6, _has(joined, "checkpoint", "checkpoints", "weights", "pretrained", "pre-trained") and _has(joined, "available", "released")),
        "declared_model_card_available": (5, _has(joined, "huggingface", "modelscope", "huggingface.co", "modelscope.cn", "modelscope.ai")),
    }
    criteria = {
        name: {"points_awarded": points if matched else 0, "max_points": points,
               "reason": "declared in paper text" if matched else "not declared in paper text"}
        for name, (points, matched) in rules.items()
    }
    return sum(item["points_awarded"] for item in criteria.values()), {"criteria": criteria}


def _missing_critical_evidence(paper_cards: list[dict[str, Any]], all_cards: list[dict[str, Any]]) -> list[str]:
    paper_fields = {card["field"] for card in paper_cards}
    all_fields = {card["field"] for card in all_cards}
    missing = [field for field in ["model.name", "task.primary_task"] if field not in paper_fields]
    if "source.repo_url" not in all_fields:
        missing.append("source.repo_url")
    if not ({"evaluation.metrics", "data.eval_datasets", "data.downstream_datasets"} & paper_fields):
        missing.append("evaluation evidence")
    return missing


def _section_name(value: Any) -> str:
    section = str(value or "unknown").strip().lower()
    if section in {"paper_title", "title"}:
        return "title"
    if "abstract" in section:
        return "abstract"
    if any(token in section for token in ("method", "approach", "architecture")):
        return "method"
    if any(token in section for token in ("experiment", "evaluation")):
        return "experiments"
    if "result" in section:
        return "results"
    return section


def _has(text: str, *words: str) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


def _cap(value: int, limit: int, caps: list[dict[str, Any]], reason: str) -> int:
    if value >= limit:
        caps.append({"cap": limit, "reason": reason, "before": value, "after": limit})
        return limit
    return value


def _decision_hint(score: int, human_review_required: bool) -> str:
    if human_review_required and score < 50:
        return "needs_human_review"
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "unknown"


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
