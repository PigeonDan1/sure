from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


FIXTURE = Path("tests/fixtures/paper_to_userspec/sample_paper.txt")


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["SURE_PAPER_TO_USERSPEC_TIMESTAMP"] = "2026-01-01T00:00:00+00:00"
    return subprocess.run(
        [sys.executable, "-m", "sure_eval.paper_to_userspec.cli", *args],
        cwd=Path.cwd(),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _build(out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return _run_cli(
        [
            "build",
            "--case-id",
            "sample",
            "--paper-text",
            str(FIXTURE),
            "--goal",
            "onboard",
            "--out",
            str(out),
            *extra,
        ]
    )


def test_cli_build_success_model_input_and_minimal_default_outputs(tmp_path: Path) -> None:
    out = tmp_path / "sample"
    result = _build(out)
    assert result.returncode == 0, result.stderr

    files = {path.name for path in out.iterdir() if path.is_file()}
    assert files == {
        "MODEL_INPUT.yaml",
        "README.md",
        "canonical_paper.md",
        "paper_confidence_report.json",
        "paper_evidence_cards.jsonl",
        "paper_parse_report.json",
        "user_spec_query.json",
        "validation_report.json",
    }
    model_input = yaml.safe_load((out / "MODEL_INPUT.yaml").read_text(encoding="utf-8"))
    assert model_input["task_type"] == "ASR"
    assert "artifact_reproducibility_score" not in model_input["confidence"]


def test_cli_pdf_parser_fallback_message(tmp_path: Path) -> None:
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    out = tmp_path / "pdf"
    result = _run_cli(
        [
            "build",
            "--case-id",
            "fake",
            "--paper",
            str(pdf),
            "--goal",
            "onboard",
            "--out",
            str(out),
        ]
    )
    assert result.returncode == 2
    assert "Fallback: extract the paper text separately" in result.stderr


def test_cli_default_does_not_output_resource_or_final_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "sample"
    result = _build(out)
    assert result.returncode == 0, result.stderr
    forbidden = {
        "resource_audit_report.json",
        "resource_evidence_cards.jsonl",
        "final_reproducibility_score.json",
        "final_evidence_cards.jsonl",
        "final_reproducibility_report.md",
        "external_evidence.json",
        "repo_summary.json",
        "model_card_summary.json",
        "review_summary.json",
        "tables.json",
        "figures_index.json",
        "parsed_sections.md",
        "evidence_map.json",
        "routing_decision.json",
    }
    assert forbidden.isdisjoint({path.name for path in out.iterdir()})
    report = json.loads((out / "paper_confidence_report.json").read_text(encoding="utf-8"))
    assert report["scope"] == "paper_only"


def test_cli_debug_artifacts_are_opt_in(tmp_path: Path) -> None:
    out = tmp_path / "debug"
    result = _build(out, "--debug-artifacts")
    assert result.returncode == 0, result.stderr
    for name in [
        "extracted_paper.txt",
        "evidence_map.json",
        "routing_decision.json",
        "parsed_sections.md",
        "tables.json",
        "figures_index.json",
    ]:
        assert (out / name).exists()


def test_cli_validate_model_input_command(tmp_path: Path) -> None:
    out = tmp_path / "sample"
    build = _build(out)
    assert build.returncode == 0, build.stderr
    result = _run_cli(["validate-model-input", "--input", str(out / "MODEL_INPUT.yaml")])
    assert result.returncode == 0, result.stderr
    assert "MODEL_INPUT validation:" in result.stdout
