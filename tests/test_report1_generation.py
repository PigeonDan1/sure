from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


EMOTION2VEC_RUN = Path("runs/paper_to_userspec/emotion2vec_mvp")


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    return subprocess.run(
        [sys.executable, "-m", "sure_eval.paper_to_userspec.cli", *args],
        cwd=Path.cwd(),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _emotion2vec_artifacts(tmp_path: Path) -> Path:
    out = tmp_path / "emotion2vec_mvp"
    out.mkdir()
    required = [
        "user_spec_query.json",
        "MODEL_INPUT.yaml",
        "validation_report.json",
        "paper_confidence_report.json",
        "paper_evidence_cards.jsonl",
        "canonical_paper.md",
    ]
    if all((EMOTION2VEC_RUN / name).exists() for name in required):
        for name in [*required, "README.md", "paper_parse_report.json"]:
            source = EMOTION2VEC_RUN / name
            if source.exists():
                shutil.copy(source, out / name)
        return out

    _write_synthetic_emotion2vec(out)
    return out


def _write_checked_audit(artifacts: Path) -> None:
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
    (artifacts / "report1_external_resource_audit.json").write_text(json.dumps(audit), encoding="utf-8")


def test_report1_cli_generates_four_files_from_emotion2vec_artifacts(tmp_path: Path) -> None:
    artifacts = _emotion2vec_artifacts(tmp_path)
    _write_checked_audit(artifacts)
    result = _run_cli(["report1", "--input-dir", str(artifacts), "--output-dir", str(artifacts)])

    assert result.returncode == 0, result.stderr
    for name in [
        "report1_screening_report.md",
        "report1_screening_report.json",
        "report1_evidence_trace.jsonl",
        "report1_external_resource_audit.json",
    ]:
        assert (artifacts / name).exists()


def test_report1_json_contains_required_sections(tmp_path: Path) -> None:
    artifacts = _emotion2vec_artifacts(tmp_path)
    _write_checked_audit(artifacts)
    result = _run_cli(["report1", "--input-dir", str(artifacts), "--output-dir", str(artifacts)])
    assert result.returncode == 0, result.stderr
    report = json.loads((artifacts / "report1_screening_report.json").read_text(encoding="utf-8"))

    assert report["report_type"] == "pre_sure_screening_report"
    assert report["report_version"] == "report1_v1"
    for key in [
        "paper_identity",
        "paper_evidence_summary",
        "sure_draft_review",
        "paper_confidence_summary",
        "external_resource_audit",
        "sure_fit_analysis",
        "scores",
        "decision",
        "limitations",
    ]:
        assert key in report
    assert report["paper_identity"]["model_name"]


def test_report1_evidence_trace_links_conclusions_to_cards(tmp_path: Path) -> None:
    artifacts = _emotion2vec_artifacts(tmp_path)
    _write_checked_audit(artifacts)
    result = _run_cli(["report1", "--input-dir", str(artifacts), "--output-dir", str(artifacts)])
    assert result.returncode == 0, result.stderr
    rows = [
        json.loads(line)
        for line in (artifacts / "report1_evidence_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert rows
    assert any(row["conclusion_id"] == "model_evidence" and row.get("evidence_card_ids") for row in rows)
    assert all(row.get("source_type") in {"paper_evidence_card", "external_resource_audit"} for row in rows)


def test_report1_without_audit_fails_fast(tmp_path: Path) -> None:
    artifacts = _emotion2vec_artifacts(tmp_path)
    result = _run_cli(["report1", "--input-dir", str(artifacts), "--output-dir", str(artifacts)])
    assert result.returncode != 0, "Expected failure when audit is missing"
    assert "Report1 requires external audit" in (result.stdout + result.stderr)
    assert not (artifacts / "report1_screening_report.json").exists()
    assert not (artifacts / "report1_screening_report.md").exists()
    assert not (artifacts / "report1_external_cache").exists()


def _write_synthetic_emotion2vec(out: Path) -> None:
    user_spec = {
        "case_id": "emotion2vec_mvp",
        "source": {
            "paper_title": "emotion2vec: Self-Supervised Speech Emotion Representation Learning",
            "repo_url": "https://github.com/ddlBoJack/emotion2vec",
            "model_card_url": None,
        },
        "model": {"name": "emotion2vec", "deployment_type": "local", "checkpoint_source": "unknown"},
        "task": {"primary_task": "SER"},
        "data": {"eval_datasets": [], "downstream_datasets": ["IEMOCAP"]},
        "evaluation": {"metrics": ["accuracy"]},
        "missing_fields": [],
        "conflict_fields": [],
        "evidence_spans": [],
    }
    model_input = {
        "model_name": "emotion2vec",
        "task_type": "SER",
        "deployment_type": "local",
        "repo": {"url": "https://github.com/ddlBoJack/emotion2vec"},
        "weights": {"source": "unknown"},
        "fixture": {"fallback_allowed": True},
        "io_contract": {
            "input_type": "audio_path",
            "output_type": "json",
            "primary_field": "label",
        },
    }
    confidence = {
        "paper_confidence_score": 58,
        "decision_hint": "C",
        "scope": "paper_only",
        "scoring_version": "paper_confidence_v1",
        "human_review_required": True,
        "declared_availability_score": 0,
        "score_breakdown": {
            "paper_field_evidence": {"criteria": {"model.name": {"points_awarded": 12}}},
            "section_coverage": {"criteria": {"abstract": {"points_awarded": 8}}},
            "declared_availability_score": {"criteria": {}},
        },
        "warnings": [],
        "caps_applied": [],
    }
    cards = [
        {"id": "paper_ev_0001", "field": "model.name", "evidence_text": "emotion2vec is proposed.", "source_type": "paper_text"},
        {"id": "paper_ev_0002", "field": "task.primary_task", "evidence_text": "speech emotion recognition.", "source_type": "paper_text"},
        {"id": "paper_ev_0003", "field": "data.downstream_datasets", "evidence_text": "IEMOCAP is used downstream.", "source_type": "paper_text"},
        {"id": "paper_ev_0004", "field": "evaluation.metrics", "evidence_text": "accuracy is reported.", "source_type": "paper_text"},
        {"id": "paper_ev_0005", "field": "source.repo_url", "evidence_text": "code link.", "source_type": "paper_text"},
    ]
    (out / "user_spec_query.json").write_text(json.dumps(user_spec), encoding="utf-8")
    (out / "MODEL_INPUT.yaml").write_text(
        "\n".join([
            "model_name: emotion2vec",
            "task_type: SER",
            "deployment_type: local",
            "repo:",
            "  url: https://github.com/ddlBoJack/emotion2vec",
            "weights:",
            "  source: unknown",
            "fixture:",
            "  fallback_allowed: true",
            "io_contract:",
            "  input_type: audio_path",
            "  output_type: json",
            "  primary_field: label",
            "",
        ]),
        encoding="utf-8",
    )
    (out / "validation_report.json").write_text(json.dumps({"status": "warning", "warnings": []}), encoding="utf-8")
    (out / "paper_confidence_report.json").write_text(json.dumps(confidence), encoding="utf-8")
    (out / "paper_evidence_cards.jsonl").write_text(
        "\n".join(json.dumps(card) for card in cards) + "\n",
        encoding="utf-8",
    )
    (out / "canonical_paper.md").write_text("# emotion2vec\n\nIEMOCAP downstream evaluation.", encoding="utf-8")
