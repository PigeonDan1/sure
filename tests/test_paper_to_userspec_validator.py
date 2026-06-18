from __future__ import annotations

import json
from pathlib import Path

from sure_eval.paper_to_userspec.converters import user_spec_to_model_input
from sure_eval.paper_to_userspec.extractor import extract_user_spec
from sure_eval.paper_to_userspec.router import route_user_spec
from sure_eval.paper_to_userspec.schema import USER_SPEC_TOP_LEVEL_FIELDS
from sure_eval.paper_to_userspec.validator import validate_model_input, validate_user_spec


FIXTURE = Path("tests/fixtures/paper_to_userspec/sample_paper.txt")


def _sample_spec() -> dict:
    return extract_user_spec(
        case_id="sample",
        paper_text=FIXTURE.read_text(encoding="utf-8"),
        raw_goal="onboard",
        paper_text_path=str(FIXTURE),
        extraction_timestamp="2026-01-01T00:00:00+00:00",
    )


def test_user_spec_schema_required_fields() -> None:
    schema = json.loads(Path("schemas/user_spec_query.schema.json").read_text(encoding="utf-8"))
    assert schema["required"] == USER_SPEC_TOP_LEVEL_FIELDS
    assert "ASR" in schema["properties"]["task"]["properties"]["primary_task"]["enum"]


def test_user_spec_validator_accepts_extracted_sample() -> None:
    spec = _sample_spec()
    report = validate_user_spec(spec)
    assert report["status"] in {"pass", "warning"}
    assert not report["blocking_errors"]
    assert spec["model"]["name"] == "ClearSpeech-ASR"


def test_model_input_required_fields_and_paper_only_confidence() -> None:
    spec = _sample_spec()
    route_user_spec(spec, repo_root=Path.cwd())
    model_input = user_spec_to_model_input(spec)
    report = validate_model_input(model_input)
    assert report["status"] in {"pass", "warning"}
    assert not report["blocking_errors"]
    assert report["can_route_to"] == "tool_onboarding"
    assert "artifact_reproducibility_score" not in model_input["confidence"]
    assert "external_review_score" not in model_input["confidence"]


def test_route_behavior() -> None:
    spec = _sample_spec()
    assert route_user_spec(spec, repo_root=Path.cwd())["route"] == "tool_onboarding"

    spec["task"]["primary_task"] = "unknown"
    decision = route_user_spec(spec, repo_root=Path.cwd())
    assert decision["route"] == "needs_human_input"
    assert "task.primary_task" in decision["missing_fields"]

    decision = route_user_spec(_sample_spec(), repo_root=Path.cwd(), route_override="needs_human_input")
    assert decision["route"] == "needs_human_input"


def test_low_quality_evidence_warning_or_fail() -> None:
    spec = _sample_spec()
    spec["evidence_spans"].append(
        {
            "field": "model.name",
            "value": "Other-ASR",
            "quote": "Other-ASR is discussed as a baseline.",
            "source": "paper_text",
            "section_name": "related work",
            "candidate_status": "selected",
            "quality_flags": ["related_work_only"],
        }
    )
    report = validate_user_spec(spec)
    messages = report["warnings"] + report["blocking_errors"]
    assert any("low-quality" in message or "related" in message for message in messages)
