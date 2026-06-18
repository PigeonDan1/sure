from __future__ import annotations

import json
from pathlib import Path

from sure_eval.paper_to_userspec.report1 import generate_report1


def test_paper_confidence_is_not_final_reproducibility_score(tmp_path: Path) -> None:
    artifacts = _artifact_dir(tmp_path, paper_confidence_score=82, declared_availability_score=0)
    report = generate_report1(input_dir=artifacts, output_dir=artifacts)

    assert report["paper_confidence_summary"]["paper_confidence_score"] == 82
    assert "final_reproducibility_score" not in report["scores"]
    assert report["scores"]["pre_sure_screening_score"] != 82
    assert "not a final reproducibility score" in report["scores"]["notes"][0]


def test_zero_declared_availability_is_explained_not_terminal(tmp_path: Path) -> None:
    artifacts = _artifact_dir(tmp_path, paper_confidence_score=72, declared_availability_score=0)
    report = generate_report1(input_dir=artifacts, output_dir=artifacts)

    assert report["paper_confidence_summary"]["declared_availability_score"] == 0
    assert any("missing declaration" in item or "parsing limitation" in item for item in report["limitations"])
    assert report["decision"]["label"] in {"B", "C"}
    assert not any("not reproducible" in reason.lower() for reason in report["decision"]["reasons"])


def test_downstream_dataset_without_eval_dataset_marks_ambiguity(tmp_path: Path) -> None:
    artifacts = _artifact_dir(
        tmp_path,
        paper_confidence_score=58,
        declared_availability_score=0,
        eval_datasets=[],
        downstream_datasets=["IEMOCAP"],
    )
    report = generate_report1(input_dir=artifacts, output_dir=artifacts)

    assert report["paper_evidence_summary"]["dataset_ambiguity"] is True
    assert "data.eval_datasets vs data.downstream_datasets" in report["sure_draft_review"]["ambiguous_fields"]
    assert report["sure_fit_analysis"]["dataset_match"] is True


def test_external_items_can_contribute_trace_without_network(tmp_path: Path) -> None:
    artifacts = _artifact_dir(tmp_path, paper_confidence_score=70, declared_availability_score=8)
    external = tmp_path / "external.json"
    external.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "ext_readme",
                        "kind": "repo_readme",
                        "source_type": "repo_file",
                        "evidence_text": "README exists.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = generate_report1(input_dir=artifacts, output_dir=artifacts, external_evidence_json=external)

    assert report["external_resource_audit"]["checks"]["readme_exists"]["status"] == "observed"
    rows = [
        json.loads(line)
        for line in (artifacts / "report1_evidence_trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(row.get("external_evidence_ids") == ["ext_readme"] for row in rows)


def _artifact_dir(
    tmp_path: Path,
    *,
    paper_confidence_score: int,
    declared_availability_score: int,
    eval_datasets: list[str] | None = None,
    downstream_datasets: list[str] | None = None,
) -> Path:
    out = tmp_path / "case"
    out.mkdir()
    eval_datasets = ["IEMOCAP"] if eval_datasets is None else eval_datasets
    downstream_datasets = [] if downstream_datasets is None else downstream_datasets
    user_spec = {
        "source": {
            "paper_title": "emotion2vec Screening",
            "repo_url": "https://github.com/example/emotion2vec",
            "model_card_url": None,
        },
        "model": {"name": "emotion2vec", "deployment_type": "local", "checkpoint_source": "unknown"},
        "task": {"primary_task": "SER"},
        "data": {"eval_datasets": eval_datasets, "downstream_datasets": downstream_datasets},
        "evaluation": {"metrics": ["accuracy"]},
        "missing_fields": [],
        "conflict_fields": [],
        "evidence_spans": [],
    }
    model_input = "\n".join(
        [
            "model_name: emotion2vec",
            "task_type: SER",
            "deployment_type: local",
            "repo:",
            "  url: https://github.com/example/emotion2vec",
            "weights:",
            "  source: unknown",
            "fixture:",
            "  fallback_allowed: true",
            "io_contract:",
            "  input_type: audio_path",
            "  output_type: json",
            "  primary_field: label",
            "",
        ]
    )
    confidence = {
        "paper_confidence_score": paper_confidence_score,
        "decision_hint": "B" if paper_confidence_score >= 70 else "C",
        "scope": "paper_only",
        "scoring_version": "paper_confidence_v1",
        "human_review_required": paper_confidence_score < 70,
        "declared_availability_score": declared_availability_score,
        "score_breakdown": {
            "paper_field_evidence": {"criteria": {"model.name": {"points_awarded": 12}}},
            "section_coverage": {"criteria": {"abstract": {"points_awarded": 8}}},
            "declared_availability_score": {"criteria": {}},
        },
        "warnings": [],
        "caps_applied": [],
    }
    cards = [
        {"id": "paper_ev_0001", "field": "model.name", "evidence_text": "emotion2vec.", "source_type": "paper_text"},
        {"id": "paper_ev_0002", "field": "task.primary_task", "evidence_text": "SER.", "source_type": "paper_text"},
        {"id": "paper_ev_0003", "field": "source.repo_url", "evidence_text": "repo URL.", "source_type": "paper_text"},
        {"id": "paper_ev_0004", "field": "evaluation.metrics", "evidence_text": "accuracy.", "source_type": "paper_text"},
    ]
    for dataset in eval_datasets:
        cards.append({"id": f"paper_ev_eval_{dataset}", "field": "data.eval_datasets", "evidence_text": dataset, "source_type": "paper_text"})
    for dataset in downstream_datasets:
        cards.append({"id": f"paper_ev_down_{dataset}", "field": "data.downstream_datasets", "evidence_text": dataset, "source_type": "paper_text"})

    (out / "user_spec_query.json").write_text(json.dumps(user_spec), encoding="utf-8")
    (out / "MODEL_INPUT.yaml").write_text(model_input, encoding="utf-8")
    (out / "validation_report.json").write_text(json.dumps({"status": "pass", "warnings": []}), encoding="utf-8")
    (out / "paper_confidence_report.json").write_text(json.dumps(confidence), encoding="utf-8")
    (out / "paper_evidence_cards.jsonl").write_text("\n".join(json.dumps(card) for card in cards) + "\n", encoding="utf-8")
    (out / "canonical_paper.md").write_text("# Screening\n", encoding="utf-8")

    # Pre-populate a checked external audit so Report1 can generate an official score.
    audit = {
        "status": "checked",
        "mode": "external_audit",
        "checks": {
            "github_repo_exists": {"status": "observed", "evidence_ids": []},
            "repo_accessible": {"status": "observed", "evidence_ids": []},
            "readme_exists": {"status": "observed", "evidence_ids": []},
            "requirements_or_env_exists": {"status": "not_checked", "evidence_ids": []},
            "license_exists": {"status": "not_checked", "evidence_ids": []},
            "checkpoint_or_weights_link_exists": {"status": "not_checked", "evidence_ids": []},
            "model_card_exists": {"status": "not_checked", "evidence_ids": []},
            "example_inference_exists": {"status": "not_checked", "evidence_ids": []},
            "recent_activity": {"status": "not_checked", "evidence_ids": []},
        },
        "external_evidence_items_count": 0,
        "warnings": [],
    }
    (out / "report1_external_resource_audit.json").write_text(json.dumps(audit), encoding="utf-8")
    return out
