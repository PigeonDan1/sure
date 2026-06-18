import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from derive_target_selection_from_claims import derive_selection
from select_paper_claims_from_tables import load_jsonl, run_selection
from validate_paper_claim_selection import run_validation, validate_candidates


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def base_query(tmp_path, tables_raw, source_pdf, content_list):
    return {
        "paper_id": "paper_x",
        "paper_title": "Paper X",
        "source_pdf": str(source_pdf),
        "mineru_content_list": str(content_list),
        "tables_raw": str(tables_raw),
        "table_preview_dir": None,
        "table_scope": {"target_table_ids": ["table_001"], "caption_keywords": [], "page_indices": []},
        "target_rows": ["ModelA"],
        "target_columns": [],
        "target_datasets": ["DatasetA"],
        "target_metrics": ["Metric1", "Metric2", "Metric3"],
        "target_models": ["ModelA"],
        "task": "TaskX",
        "split": "test",
        "metric_directions": {
            "Metric1": "higher_is_better",
            "Metric2": "lower_is_better",
            "Metric3": "higher_is_better",
        },
        "paper_value_unit": "points",
        "expected_claim_count": 3,
        "key_aliases": {
            "metrics": {
                "Metric1": ["Metric1", "M1"],
                "Metric2": ["Metric2", "M2"],
                "Metric3": ["Metric3", "M3"],
            },
            "datasets": {"DatasetA": ["DatasetA"]},
            "models": {"ModelA": ["ModelA"]},
            "rows": {"ModelA": ["ModelA"]},
        },
        "validation_policy": {
            "require_table_match": True,
            "require_row_key_match": True,
            "require_metric_key_match": True,
            "require_dataset_key_match": True,
            "require_model_key_match": True,
            "require_value_in_raw_table": True,
            "require_source_pdf_exists": True,
            "allow_human_review": True,
        },
    }


def synthetic_html(values=("10.1", "20.2", "30.3")):
    return (
        "<table>"
        "<tr><td rowspan=\"2\">System</td><td colspan=\"3\">DatasetA</td></tr>"
        "<tr><td>Metric1</td><td>Metric2</td><td>Metric3</td></tr>"
        "<tr><td>ModelA</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        "</table>"
    ) % values


def build_case(tmp_path, raw_html=None):
    raw_html = raw_html or synthetic_html()
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    content_list = tmp_path / "content_list.json"
    write_json(content_list, [])
    tables_raw = tmp_path / "tables_raw.jsonl"
    write_jsonl(tables_raw, [{
        "table_id": "table_001",
        "caption": "Synthetic result table",
        "page_idx": 1,
        "raw_table": raw_html,
    }])
    query = base_query(tmp_path, tables_raw, source_pdf, content_list)
    query_path = tmp_path / "query.json"
    write_json(query_path, query)
    return query_path, query


def test_multi_header_html_key_matching_generic(tmp_path):
    query_path, _query = build_case(tmp_path)
    out_dir = tmp_path / "out"
    run_selection(str(query_path), str(out_dir))
    candidates = load_jsonl(out_dir / "paper_claim_candidates.jsonl")
    assert len(candidates) == 3
    assert [candidate["paper_value"] for candidate in candidates] == [10.1, 20.2, 30.3]
    assert {candidate["dataset_key_normalized"] for candidate in candidates} == {"dataseta"}


def test_query_driven_case_specific_keys(tmp_path):
    query_path, query = build_case(tmp_path)
    query["target_metrics"] = ["Metric2"]
    query["expected_claim_count"] = 1
    write_json(query_path, query)
    out_dir = tmp_path / "out"
    run_selection(str(query_path), str(out_dir))
    candidates = load_jsonl(out_dir / "paper_claim_candidates.jsonl")
    assert len(candidates) == 1
    assert candidates[0]["metric_key_normalized"] == "metric2"
    assert candidates[0]["paper_value"] == 20.2


def test_selector_does_not_use_legacy_selection_as_input(tmp_path):
    query_path, _query = build_case(tmp_path, synthetic_html(("11.0", "22.0", "33.0")))
    legacy = tmp_path / "legacy.json"
    write_json(legacy, {"wrong_value": 999.0})
    out_dir = tmp_path / "out"
    run_selection(str(query_path), str(out_dir), str(legacy))
    candidates = load_jsonl(out_dir / "paper_claim_candidates.jsonl")
    assert [candidate["paper_value"] for candidate in candidates] == [11.0, 22.0, 33.0]
    manifest = json.loads((out_dir / "claim_selection_manifest.json").read_text(encoding="utf-8"))
    assert manifest["legacy_selection_used_as_input"] is False


def test_requires_human_review_when_required_key_missing(tmp_path):
    query_path, query = build_case(tmp_path)
    candidate = {
        "table_id": "table_001",
        "paper_value_raw": "10.1",
        "row_key_normalized": "modela",
        "metric_key_normalized": "metric1",
        "model_key_normalized": "modela",
        "dataset_key_normalized": None,
        "metric_direction": "higher_is_better",
        "notes": [],
    }
    table_records = {"table_001": {"table_id": "table_001", "caption": "", "raw_table": synthetic_html()}}
    claims = validate_candidates(query, [candidate], table_records)
    assert claims[0]["validation_status"] != "validated"
    assert "dataset_key_evidence" in claims[0]["validation_issues"]


def test_metric_direction_from_query(tmp_path):
    query_path, _query = build_case(tmp_path)
    out_dir = tmp_path / "out"
    run_selection(str(query_path), str(out_dir))
    candidates = load_jsonl(out_dir / "paper_claim_candidates.jsonl")
    by_metric = {candidate["metric_key_normalized"]: candidate for candidate in candidates}
    assert by_metric["metric2"]["metric_direction"] == "lower_is_better"
    assert by_metric["metric1"]["metric_direction"] == "higher_is_better"


def test_selection_artifact_is_derived_from_validated_claims(tmp_path):
    query_path, _query = build_case(tmp_path)
    out_dir = tmp_path / "out"
    run_selection(str(query_path), str(out_dir))
    run_validation(str(query_path), str(out_dir / "paper_claim_candidates.jsonl"), str(out_dir))
    selection_path = out_dir / "selection.json"
    derive_selection(str(out_dir / "paper_claims_validated.json"), str(query_path), str(selection_path))
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    assert selection["source_of_truth"] == str(out_dir / "paper_claims_validated.json")
    assert selection["generation_method"] == "schema_first_key_matching_plus_validation"
    assert len(selection["selected_claims"]) == 3
