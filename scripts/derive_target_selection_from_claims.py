#!/usr/bin/env python3
"""Derive a case-level target selection artifact from validated paper claims."""

import argparse
import json
import shutil
from pathlib import Path


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def backup_existing_out(out_path):
    out_path = Path(out_path)
    if not out_path.exists():
        return None
    legacy_path = out_path.with_name(out_path.stem + ".legacy" + out_path.suffix)
    if legacy_path.exists():
        index = 1
        while True:
            candidate = out_path.with_name(out_path.stem + ".bak%d" % index + out_path.suffix)
            if not candidate.exists():
                shutil.copy2(str(out_path), str(candidate))
                return str(candidate)
            index += 1
    shutil.copy2(str(out_path), str(legacy_path))
    return str(legacy_path)


def derive_selection(validated_claims_path, query_path, out_path, legacy_selection=None):
    validated = load_json(validated_claims_path)
    query = load_json(query_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = backup_existing_out(out_path)
    canonical_legacy = out_path.with_name(out_path.stem + ".legacy" + out_path.suffix)
    if canonical_legacy.exists():
        legacy_reference = str(canonical_legacy)
    else:
        legacy_reference = backup_path or legacy_selection

    claims = validated.get("claims") or []
    selected_claims = [
        claim for claim in claims
        if claim.get("validation_status") in ("validated", "requires_human_review")
    ]
    selected_metrics = []
    for claim in selected_claims:
        metric = claim.get("metric_key")
        if metric and metric not in selected_metrics:
            selected_metrics.append(metric)
    validation_summary = {
        "status": validated.get("status"),
        "validated_claim_count": validated.get("validated_claim_count"),
        "requires_human_review_count": validated.get("requires_human_review_count"),
        "failed_claim_count": validated.get("failed_claim_count"),
        "selected_claim_count": len(selected_claims),
    }
    artifact = {
        "status": "success" if selected_claims else "failed",
        "generation_method": "schema_first_key_matching_plus_validation",
        "source_of_truth": str(validated_claims_path),
        "legacy_reference": legacy_reference,
        "paper_id": query.get("paper_id"),
        "paper_title": query.get("paper_title"),
        "source_pdf": query.get("source_pdf"),
        "selected_claims": selected_claims,
        "selected_metrics": selected_metrics,
        "validation_summary": validation_summary,
        "evidence_files": {
            "validated_claims": str(validated_claims_path),
            "query": str(query_path),
            "tables_raw": query.get("tables_raw"),
            "mineru_content_list": query.get("mineru_content_list"),
            "table_preview_dir": query.get("table_preview_dir"),
        },
        "warnings": [
            "Legacy selection was not used as extraction input.",
            "Use validated claims as source of truth; review any claim with needs_human_review=true.",
        ],
        "notes": [
            "This case-level artifact was derived from validated paper claims.",
        ],
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(artifact, f, ensure_ascii=False, indent=2, sort_keys=True)
    return {
        "out": str(out_path),
        "backup": backup_path,
        "selected_claim_count": len(selected_claims),
        "status": artifact["status"],
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Derive case-level paper target selection from validated claims.")
    parser.add_argument("--validated-claims", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--legacy-selection")
    return parser.parse_args()


def main():
    args = parse_args()
    result = derive_selection(args.validated_claims, args.query, args.out, args.legacy_selection)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
