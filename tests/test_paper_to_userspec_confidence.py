from __future__ import annotations

from pathlib import Path

from sure_eval.paper_to_userspec import confidence as confidence_module
from sure_eval.paper_to_userspec.confidence import confidence_for_user_spec, spans_to_paper_evidence_cards
from sure_eval.paper_to_userspec.extractor import extract_user_spec


FIXTURE = Path("tests/fixtures/paper_to_userspec/sample_paper.txt")


def _confidence_for(text: str) -> tuple[dict, dict]:
    spec = extract_user_spec(case_id="case", paper_text=text, raw_goal="onboard")
    cards = spans_to_paper_evidence_cards(spec["evidence_spans"])
    return confidence_for_user_spec(spec, cards)


def test_fixed_055_confidence_is_removed() -> None:
    spec = extract_user_spec(
        case_id="sample",
        paper_text=FIXTURE.read_text(encoding="utf-8"),
        raw_goal="onboard",
    )
    assert spec["confidence"]["overall_percent"] != 55
    assert spec["confidence"]["scoring_version"] == "paper_confidence_v1"


def test_paper_confidence_ignores_resource_and_review_cards() -> None:
    spec = extract_user_spec(
        case_id="sample",
        paper_text=FIXTURE.read_text(encoding="utf-8"),
        raw_goal="onboard",
    )
    paper_cards = spans_to_paper_evidence_cards(spec["evidence_spans"])
    baseline, _ = confidence_for_user_spec(spec, paper_cards)
    noisy_external_cards = [
        {
            "id": "resource_ev_0001",
            "field": "repo.has_readme_or_docs",
            "source_type": "repo_file",
            "evidence_text": "README and inference.py exist.",
        },
        {
            "id": "review_ev_0001",
            "field": "external.review",
            "source_type": "review_comment",
            "evidence_text": "The artifact is reproducible.",
        },
    ]
    with_external, _ = confidence_for_user_spec(spec, [*paper_cards, *noisy_external_cards])

    assert with_external["overall_percent"] == baseline["overall_percent"]
    assert not hasattr(confidence_module, "compute_resource_audit_score")
    assert not hasattr(confidence_module, "compute_final_reproducibility_score")


def test_declared_availability_boundaries_are_paper_only() -> None:
    text = """# Declared ASR

## Abstract
Declared-ASR is an automatic speech recognition model.

## Method
Code and checkpoints are available at https://github.com/example/declared-asr.
The model card is hosted on https://huggingface.co/example/declared-asr.

## Experiments
We report WER on LibriSpeech.
"""
    _conf, report = _confidence_for(text)
    criteria = report["score_breakdown"]["declared_availability_score"]["criteria"]
    assert criteria["declared_code_available"]["points_awarded"] == 8
    assert criteria["declared_checkpoint_available"]["points_awarded"] == 6
    assert criteria["declared_model_card_available"]["points_awarded"] == 5

    no_model_card = text.replace("https://huggingface.co/example/declared-asr", "the project page")
    _conf, report = _confidence_for(no_model_card)
    criteria = report["score_breakdown"]["declared_availability_score"]["criteria"]
    assert criteria["declared_model_card_available"]["points_awarded"] == 0


def test_abstract_only_cap() -> None:
    text = """# Abstract Cap ASR

## Abstract
Abstract-Cap-ASR is an automatic speech recognition model. Code is available at
https://github.com/example/abstract-cap-asr. We report WER on LibriSpeech.
"""
    conf, report = _confidence_for(text)
    assert conf["overall_percent"] <= 60
    assert any(cap["reason"] == "abstract_only_evidence" for cap in report["caps_applied"])


def test_missing_critical_evidence_cap() -> None:
    text = """# Missing Repo ASR

## Abstract
Missing-Repo-ASR is an automatic speech recognition model.

## Experiments
We report WER on LibriSpeech.
"""
    conf, report = _confidence_for(text)
    assert conf["overall_percent"] <= 55
    assert any(cap["reason"] == "missing_critical_paper_evidence" for cap in report["caps_applied"])
