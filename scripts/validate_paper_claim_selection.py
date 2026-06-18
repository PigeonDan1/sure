#!/usr/bin/env python3
"""Validate selected paper claim candidates against MinerU evidence files."""

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from select_paper_claims_from_tables import load_json, load_jsonl, normalize_key, normalize_targets


def table_records_by_id(path):
    records = load_jsonl(path)
    return {record.get("table_id"): record for record in records}


def preview_exists(query, table_id):
    preview_dir = query.get("table_preview_dir")
    if not preview_dir:
        return False
    return (Path(preview_dir) / ("%s.md" % table_id)).exists()


def required_checks(candidate, query, table_records):
    policy = query.get("validation_policy") or {}
    table = table_records.get(candidate.get("table_id"))
    raw_table = table.get("raw_table") if table else ""
    checks = {
        "source_pdf_exists": (not policy.get("require_source_pdf_exists", True)) or Path(query.get("source_pdf", "")).exists(),
        "content_list_exists": Path(query.get("mineru_content_list", "")).exists(),
        "tables_raw_exists": Path(query.get("tables_raw", "")).exists(),
        "table_id_exists": table is not None,
        "caption_keyword_match": caption_keyword_match(table, query) if table else False,
        "value_in_raw_table": (not policy.get("require_value_in_raw_table", True)) or str(candidate.get("paper_value_raw")) in (raw_table or ""),
        "row_key_evidence": bool(candidate.get("row_key_normalized")) or not policy.get("require_row_key_match", True),
        "metric_key_evidence": bool(candidate.get("metric_key_normalized")) or not policy.get("require_metric_key_match", True),
        "dataset_key_evidence": bool(candidate.get("dataset_key_normalized")) or not policy.get("require_dataset_key_match", False),
        "model_key_evidence": bool(candidate.get("model_key_normalized")) or not policy.get("require_model_key_match", False),
        "metric_direction_resolved": bool(candidate.get("metric_direction")),
        "table_preview_exists": preview_exists(query, candidate.get("table_id")),
    }
    if not ((query.get("table_scope") or {}).get("caption_keywords")):
        checks["caption_keyword_match"] = True
    return checks


def caption_keyword_match(table, query):
    keywords = ((query.get("table_scope") or {}).get("caption_keywords")) or []
    if not keywords:
        return True
    text = "%s %s" % (table.get("caption") or "", table.get("raw_table") or "")
    text = text.lower()
    return all(str(keyword).lower() in text for keyword in keywords)


def validation_method(candidate, checks):
    notes = candidate.get("notes") or []
    if any("plain_text_fallback" in note or "no_structured" in note for note in notes):
        return "parser_incomplete_requires_human_review"
    if checks.get("table_preview_exists") and checks.get("source_pdf_exists"):
        return "raw_html_plus_mineru_preview_plus_source_pdf_presence_check"
    if checks.get("content_list_exists") and checks.get("source_pdf_exists"):
        return "raw_html_plus_content_list_plus_source_pdf_presence_check"
    return "raw_table_only_requires_human_review"


def validation_status_and_confidence(candidate, checks, query):
    policy = query.get("validation_policy") or {}
    required = [
        "tables_raw_exists",
        "table_id_exists",
        "value_in_raw_table",
        "metric_direction_resolved",
    ]
    if policy.get("require_source_pdf_exists", True):
        required.append("source_pdf_exists")
    if policy.get("require_row_key_match", True):
        required.append("row_key_evidence")
    if policy.get("require_metric_key_match", True):
        required.append("metric_key_evidence")
    if policy.get("require_dataset_key_match", False):
        required.append("dataset_key_evidence")
    if policy.get("require_model_key_match", False):
        required.append("model_key_evidence")
    if (query.get("table_scope") or {}).get("caption_keywords"):
        required.append("caption_keyword_match")

    missing = [name for name in required if not checks.get(name)]
    parser_notes = [note for note in candidate.get("notes") or [] if "parser" in note or "fallback" in note]
    if missing:
        return "failed", "low", True, missing
    if parser_notes:
        return "requires_human_review", "low", True, parser_notes
    if not checks.get("table_preview_exists"):
        return "validated", "medium", False, []
    return "validated", "high", False, []


def validate_candidates(query, candidates, table_records):
    validated = []
    for candidate in candidates:
        checks = required_checks(candidate, query, table_records)
        method = validation_method(candidate, checks)
        status, confidence, needs_review, issues = validation_status_and_confidence(candidate, checks, query)
        updated = dict(candidate)
        updated["validation_status"] = status
        updated["validation_method"] = method
        updated["confidence"] = confidence
        updated["needs_human_review"] = bool(needs_review)
        updated["validation_checks"] = checks
        updated["validation_issues"] = issues
        notes = list(updated.get("notes") or [])
        if method.endswith("presence_check"):
            notes.append("Validation checks evidence file presence and raw table content; it is not PDF visual validation.")
        if needs_review:
            notes.append("Human review is required or recommended by validation policy/check results.")
        updated["notes"] = sorted(set(notes))
        validated.append(updated)
    return validated


def evidence_card(candidate):
    return {
        "claim_id": "%s:%s:%s:%s:%s" % (
            candidate.get("paper_id"),
            candidate.get("table_id"),
            candidate.get("row_key_normalized"),
            candidate.get("dataset_key_normalized"),
            candidate.get("metric_key_normalized"),
        ),
        "paper_id": candidate.get("paper_id"),
        "table_id": candidate.get("table_id"),
        "table_caption": candidate.get("table_caption"),
        "page_idx": candidate.get("table_page_idx"),
        "row_key": candidate.get("row_key"),
        "dataset_key": candidate.get("dataset_key"),
        "model_key": candidate.get("model_key"),
        "metric_key": candidate.get("metric_key"),
        "paper_value_raw": candidate.get("paper_value_raw"),
        "paper_value": candidate.get("paper_value"),
        "header_path": candidate.get("header_path"),
        "evidence_cell": candidate.get("evidence_cell"),
        "validation_status": candidate.get("validation_status"),
        "validation_method": candidate.get("validation_method"),
        "confidence": candidate.get("confidence"),
        "needs_human_review": candidate.get("needs_human_review"),
    }


def write_jsonl(path, records):
    with Path(path).open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def build_report(query, claims, legacy_selection):
    lines = []
    lines.append("# Claim Validation Report")
    lines.append("")
    lines.append("## Query Summary")
    lines.append("")
    lines.append("- paper_id: `%s`" % query.get("paper_id"))
    lines.append("- paper_title: `%s`" % query.get("paper_title"))
    lines.append("- task: `%s`" % query.get("task"))
    lines.append("- split: `%s`" % query.get("split"))
    lines.append("- expected_claim_count: `%s`" % query.get("expected_claim_count"))
    lines.append("")
    lines.append("## Source PDF")
    lines.append("")
    lines.append("- `%s`" % query.get("source_pdf"))
    lines.append("")
    lines.append("## MinerU Evidence Paths")
    lines.append("")
    lines.append("- content_list: `%s`" % query.get("mineru_content_list"))
    lines.append("- tables_raw: `%s`" % query.get("tables_raw"))
    lines.append("- table_preview_dir: `%s`" % query.get("table_preview_dir"))
    lines.append("")
    lines.append("## Tables Inspected")
    lines.append("")
    for table_id in sorted(set(claim.get("table_id") for claim in claims)):
        lines.append("- `%s`" % table_id)
    lines.append("")
    lines.append("## Candidate Claims")
    lines.append("")
    for claim in claims:
        lines.append("- table=%s row=%s dataset=%s model=%s metric=%s value=%s" % (
            claim.get("table_id"),
            claim.get("row_key"),
            claim.get("dataset_key"),
            claim.get("model_key"),
            claim.get("metric_key"),
            claim.get("paper_value_raw"),
        ))
    lines.append("")
    lines.append("## Validated Claims")
    lines.append("")
    for claim in claims:
        lines.append("- metric=%s value=%s status=%s confidence=%s human_review=%s" % (
            claim.get("metric_key"),
            claim.get("paper_value_raw"),
            claim.get("validation_status"),
            claim.get("confidence"),
            claim.get("needs_human_review"),
        ))
    lines.append("")
    lines.append("## Key Matching Explanation")
    lines.append("")
    lines.append("Rows, columns, datasets, models, and metrics were normalized from table cell text and header paths, then matched to query keys and query-provided aliases.")
    lines.append("")
    lines.append("## Validation Method Explanation")
    lines.append("")
    lines.append("Validation checks raw table values, required key evidence, MinerU content/table files, optional previews, and source PDF presence. It does not perform PDF page visual validation.")
    lines.append("")
    lines.append("## Human Review Requirement")
    lines.append("")
    if any(claim.get("needs_human_review") for claim in claims):
        lines.append("At least one claim requires or recommends human review.")
    else:
        lines.append("No claim was marked as requiring human review by the current validation policy.")
    lines.append("")
    lines.append("## Legacy Selection Comparison")
    lines.append("")
    if legacy_selection:
        lines.append("Legacy selection was provided only as a comparison reference: `%s`." % legacy_selection)
    else:
        lines.append("No legacy selection was provided.")
    lines.append("")
    lines.append("## Known Limitations")
    lines.append("")
    lines.append("- Markdown parsing does not support row or column spans.")
    lines.append("- Plain text tables cannot produce validated structured claims.")
    lines.append("- PDF visual validation is not implemented.")
    lines.append("")
    lines.append("## Recommended Next Action")
    lines.append("")
    lines.append("Use `paper_claims_validated.json` as the source of truth for downstream case-level selection derivation. Review any claim marked `needs_human_review=true`.")
    lines.append("")
    return "\n".join(lines)


def run_validation(query_path, candidates_path, out_dir, legacy_selection=None):
    query = load_json(query_path)
    candidates = load_jsonl(candidates_path)
    table_records = table_records_by_id(query["tables_raw"])
    claims = validate_candidates(query, candidates, table_records)

    status = "success"
    if any(claim.get("validation_status") == "failed" for claim in claims):
        status = "failed"
    elif any(claim.get("validation_status") == "requires_human_review" for claim in claims):
        status = "requires_human_review"

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    validated_path = out_dir / "paper_claims_validated.json"
    cards_path = out_dir / "claim_evidence_cards.jsonl"
    report_path = out_dir / "claim_validation_report.md"
    payload = {
        "status": status,
        "query_path": str(query_path),
        "candidate_claim_count": len(candidates),
        "validated_claim_count": sum(1 for claim in claims if claim.get("validation_status") == "validated"),
        "requires_human_review_count": sum(1 for claim in claims if claim.get("needs_human_review")),
        "failed_claim_count": sum(1 for claim in claims if claim.get("validation_status") == "failed"),
        "legacy_selection_used_as_input": False,
        "claims": claims,
        "notes": [
            "Validation used raw MinerU table evidence and evidence file presence checks.",
            "No PDF visual validation was performed.",
        ],
    }
    with validated_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    write_jsonl(cards_path, [evidence_card(claim) for claim in claims])
    with report_path.open("w", encoding="utf-8") as f:
        f.write(build_report(query, claims, legacy_selection))
    return {
        "paper_claims_validated": str(validated_path),
        "claim_evidence_cards": str(cards_path),
        "claim_validation_report": str(report_path),
        "status": status,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Validate paper claim candidates.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--legacy-selection")
    return parser.parse_args()


def main():
    args = parse_args()
    result = run_validation(args.query, args.candidates, args.out_dir, args.legacy_selection)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
