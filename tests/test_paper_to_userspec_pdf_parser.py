from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sure_eval.paper_to_userspec import io


class _FakePage:
    def extract_text(self) -> str:
        return (
            "Abstract\n"
            "This paper describes a speech model with enough text for extraction.\n"
            "Introduction\n"
            "The method and experiments include datasets, metrics, and references.\n"
            "References\n"
            "A. Example."
        )


class _FakeReader:
    def __init__(self, path: str) -> None:
        self.path = path
        self.pages = [_FakePage()]


def _write_fake_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4\nfake\n")


def _fake_completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["mineru"], returncode, stdout=stdout, stderr=stderr)


def test_pdf_path_and_out_dir_resolve_to_absolute(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "paper.pdf"
    out = tmp_path / "relative_out"
    _write_fake_pdf(pdf)
    monkeypatch.setattr(io, "PdfReader", _FakeReader)

    text, report = io.parse_pdf_to_artifacts(pdf, out, parser_name="pypdf")

    assert text
    assert Path(report["pdf_path"]).is_absolute()
    assert Path(report["out_dir"]).is_absolute()
    assert report["requested_parser"] == "pypdf"
    assert report["actual_parser"] == "pypdf"


def test_nonzero_mineru_failure_writes_report_and_logs(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "paper.pdf"
    out = tmp_path / "out"
    mineru_bin = tmp_path / "mineru"
    _write_fake_pdf(pdf)
    mineru_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    mineru_bin.chmod(0o755)
    monkeypatch.setenv("SURE_PAPER_MINERU_BIN", str(mineru_bin))
    monkeypatch.setenv("SURE_PAPER_MINERU_MODEL_SOURCE", "modelscope")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command == [str(mineru_bin.resolve()), "--version"]:
            return _fake_completed(0, stdout="mineru, version test\n")
        return _fake_completed(7, stdout="full stdout", stderr="full stderr")

    monkeypatch.setattr(io.subprocess, "run", fake_run)

    try:
        io.parse_pdf_to_artifacts(pdf, out, parser_name="mineru")
    except RuntimeError as exc:
        assert "exit code 7" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")

    report = json.loads((out / "paper_parse_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["requested_parser"] == "mineru"
    assert report["actual_parser"] == "mineru"
    assert report["error_type"] == "mineru_nonzero_exit"
    assert report["returncode"] == 7
    assert report["mineru_executable"] == str(mineru_bin.resolve())
    assert report["selected_env"]["MINERU_MODEL_SOURCE"] == "modelscope"
    assert (out / "mineru_stdout.log").read_text(encoding="utf-8") == "full stdout"
    assert (out / "mineru_stderr.log").read_text(encoding="utf-8") == "full stderr"
    assert calls[0] == [str(mineru_bin.resolve()), "--version"]


def test_mineru_first_failure_falls_back_to_pypdf(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "paper.pdf"
    out = tmp_path / "out"
    _write_fake_pdf(pdf)
    monkeypatch.delenv("SURE_PAPER_MINERU_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty_path"))
    monkeypatch.setattr(io, "PdfReader", _FakeReader)

    text, report = io.parse_pdf_to_artifacts(pdf, out, parser_name="mineru-first")

    assert text
    assert report["status"] == "success_with_fallback"
    assert report["requested_parser"] == "mineru-first"
    assert report["preferred_parser"] == "mineru"
    assert report["actual_parser"] == "pypdf"
    assert report["parser_name"] == "pypdf"
    assert report["fallback_used"] is True
    assert "MinerU" in report["fallback_reason"] or "MinerU" in report["warnings"][-1]
    assert report["mineru_failure_summary"]["actual_parser"] == "mineru"

    written = json.loads((out / "paper_parse_report.json").read_text(encoding="utf-8"))
    assert written["actual_parser"] == "pypdf"
    assert written["parser_name"] == "pypdf"
    assert written["fallback_used"] is True


def test_input_pdf_missing_writes_failure_report(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    out = tmp_path / "out"

    try:
        io.parse_pdf_to_artifacts(missing, out, parser_name="mineru")
    except RuntimeError as exc:
        assert "does not exist" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")

    report = json.loads((out / "paper_parse_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["requested_parser"] == "mineru"
    assert report["actual_parser"] is None
    assert report["error_type"] == "input_pdf_missing"
