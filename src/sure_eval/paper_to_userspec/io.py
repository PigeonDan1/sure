"""File IO helpers for Paper_to_UserSpec artifacts."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

import yaml

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - exercised by monkeypatch in tests
    PdfReader = None  # type: ignore[assignment]

MIN_EXTRACTED_PDF_CHARS = 80
DEFAULT_MINERU_TIMEOUT_SEC = 600
PDFParserName = Literal["pypdf", "mineru", "auto"]
SECTION_PATTERNS = [
    ("abstract", r"\babstract\b"),
    ("introduction", r"\b(?:1\.?\s*)?introduction\b"),
    ("method", r"\b(?:method|methodology|approach|model architecture)\b"),
    ("experiments", r"\b(?:experiments|experimental setup|evaluation)\b"),
    ("results", r"\bresults\b"),
    ("conclusion", r"\bconclusions?\b"),
    ("references", r"\breferences\b"),
]


def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def write_text(path: str | Path, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def write_json(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def write_yaml(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)


def parse_pdf_to_artifacts(
    pdf_path: str | Path,
    out_dir: str | Path,
    parser_name: PDFParserName = "pypdf",
) -> tuple[str, dict[str, Any]]:
    """Parse a PDF into canonical artifacts consumed by the extractor."""
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    if parser_name == "pypdf":
        text, report = _parse_pdf_with_pypdf(pdf_path, out_dir)
    elif parser_name == "mineru":
        text, report = _parse_pdf_with_mineru(pdf_path, out_dir)
    elif parser_name == "auto":
        text, report = _parse_pdf_auto(pdf_path, out_dir)
    else:
        raise RuntimeError(f"Unsupported PDF parser: {parser_name}")

    write_json(out_dir / "paper_parse_report.json", report)
    return text, report


def canonicalize_paper_text(
    paper_text: str,
    *,
    input_source: str | Path | None,
    out_dir: str | Path,
    parser_name: str = "paper_text",
) -> tuple[str, dict[str, Any]]:
    """Write canonical paper artifacts for already-extracted text input."""
    out_dir = Path(out_dir)
    canonical = _normalize_canonical_markdown(paper_text)
    canonical_path = out_dir / "canonical_paper.md"
    extracted_path = out_dir / "extracted_paper.txt"
    write_text(canonical_path, canonical)
    write_text(extracted_path, _markdown_to_plain_text(canonical))
    report = _build_parse_report(
        parser_name=parser_name,
        parser_version=None,
        input_pdf=str(input_source) if input_source else None,
        output_markdown_path=canonical_path,
        output_json_path=None,
        num_pages=None,
        text=canonical,
        warnings=[],
    )
    write_json(out_dir / "paper_parse_report.json", report)
    return canonical, report


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """Extract text from a text-based PDF using optional pypdf."""
    text, _metadata = extract_text_from_pdf_with_report(pdf_path)
    return text


def extract_text_from_pdf_with_report(pdf_path: str | Path) -> tuple[str, dict[str, Any]]:
    """Extract text from a text-based PDF and return lightweight metadata."""
    pdf_path = Path(pdf_path)
    metadata: dict[str, Any] = {
        "enabled": True,
        "status": "fail",
        "num_pages": None,
        "extracted_chars": 0,
        "warning": None,
    }
    if not pdf_path.exists():
        metadata["warning"] = f"PDF file does not exist: {pdf_path}"
        raise RuntimeError(metadata["warning"])
    if pdf_path.suffix.lower() != ".pdf":
        metadata["warning"] = f"Expected a .pdf file, got: {pdf_path}"
        raise RuntimeError(metadata["warning"])
    if PdfReader is None:
        metadata["warning"] = (
            "PDF parsing requires pypdf. Install it with: uv pip install pypdf or "
            "pip install pypdf. Alternatively use --paper-text."
        )
        raise RuntimeError(metadata["warning"])

    reader = PdfReader(str(pdf_path))
    pages = list(getattr(reader, "pages", []))
    metadata["num_pages"] = len(pages)
    page_texts = []
    for page in pages:
        page_text = page.extract_text() or ""
        page_texts.append(_lightly_normalize_pdf_text(page_text))
    text = "\n\n".join(page_texts).strip()
    metadata["extracted_chars"] = len(text)
    if len(text) < MIN_EXTRACTED_PDF_CHARS:
        metadata["warning"] = (
            "No sufficient text extracted from PDF. This may be a scanned PDF. "
            "OCR is not supported in this MVP. Please provide --paper-text."
        )
        raise RuntimeError(metadata["warning"])
    metadata["status"] = "pass"
    return text, metadata


def _parse_pdf_with_pypdf(pdf_path: Path, out_dir: Path) -> tuple[str, dict[str, Any]]:
    text, metadata = extract_text_from_pdf_with_report(pdf_path)
    canonical = _normalize_canonical_markdown(text)
    canonical_path = out_dir / "canonical_paper.md"
    extracted_path = out_dir / "extracted_paper.txt"
    write_text(canonical_path, canonical)
    write_text(extracted_path, _markdown_to_plain_text(canonical))
    report = _build_parse_report(
        parser_name="pypdf",
        parser_version=_pypdf_version(),
        input_pdf=str(pdf_path),
        output_markdown_path=canonical_path,
        output_json_path=None,
        num_pages=metadata.get("num_pages"),
        text=canonical,
        warnings=[metadata["warning"]] if metadata.get("warning") else [],
    )
    return canonical, report


def _parse_pdf_auto(pdf_path: Path, out_dir: Path) -> tuple[str, dict[str, Any]]:
    try:
        text, report = _parse_pdf_with_pypdf(pdf_path, out_dir)
    except RuntimeError as exc:
        warnings = [f"pypdf failed in auto mode: {exc}"]
    else:
        warnings = list(report.get("warnings", []))
        if not _should_try_mineru(text, report):
            report["parser_name"] = "pypdf"
            report["warnings"] = warnings
            write_json(out_dir / "paper_parse_report.json", report)
            return text, report
        warnings.append("pypdf parse quality was low; auto mode is trying MinerU.")

    try:
        mineru_text, mineru_report = _parse_pdf_with_mineru(pdf_path, out_dir)
    except RuntimeError as exc:
        raise RuntimeError(
            "Auto PDF parsing could not obtain a reliable parse. pypdf output was too short, "
            "missing expected paper sections, looked corrupted, or appeared to be scanned; "
            f"MinerU fallback is unavailable or failed: {exc}. "
            "Install the optional local MinerU CLI or provide extracted text with --paper-text."
        ) from exc
    mineru_report["warnings"] = warnings + list(mineru_report.get("warnings", []))
    write_json(out_dir / "paper_parse_report.json", mineru_report)
    return mineru_text, mineru_report


def _parse_pdf_with_mineru(pdf_path: Path, out_dir: Path) -> tuple[str, dict[str, Any]]:
    _validate_pdf_path(pdf_path)
    cli = _mineru_cli()
    if cli is None:
        raise RuntimeError(
            "MinerU PDF parser is not available. Could not find local CLI command "
            "'mineru' or legacy 'magic-pdf'. Install the optional local MinerU CLI "
            "outside the default dependencies, then rerun with --pdf-parser mineru, "
            "or use --paper-text as a fallback."
        )

    raw_dir = out_dir / "mineru_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    version = _command_version(cli["path"])
    timeout_sec = _mineru_timeout_sec()
    stdout_tail = ""
    stderr_tail = ""
    with tempfile.TemporaryDirectory(prefix="paper_to_userspec_mineru_") as tmp:
        tmp_out = Path(tmp) / "out"
        tmp_out.mkdir(parents=True, exist_ok=True)
        run_command = _mineru_run_command(cli, pdf_path, tmp_out)
        try:
            proc = subprocess.run(
                run_command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_tail = _tail_text(exc.stdout)
            stderr_tail = _tail_text(exc.stderr)
            _write_mineru_failure_report(
                out_dir=out_dir,
                raw_dir=raw_dir,
                cli=cli,
                version=version,
                pdf_path=pdf_path,
                timeout_sec=timeout_sec,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
            raise RuntimeError(
                f"MinerU parsing timed out after {timeout_sec} seconds. "
                "Try --pdf-parser pypdf, --pdf-parser auto, or --paper-text."
            ) from exc
        stdout_tail = _tail_text(proc.stdout)
        stderr_tail = _tail_text(proc.stderr)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"MinerU CLI failed with exit code {proc.returncode}: {detail}")
        for generated in tmp_out.rglob("*"):
            if generated.is_file():
                target = raw_dir / generated.relative_to(tmp_out)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(generated.read_bytes())

    markdown_candidates = _find_files(raw_dir, [".md", ".markdown"])
    markdown_path = _largest_file(markdown_candidates)
    json_candidates = _find_files(raw_dir, [".json"])
    json_path = _largest_file(json_candidates)
    json_text = _extract_text_from_mineru_json_candidates(json_candidates)
    warnings: list[str] = []
    if markdown_path:
        canonical = _normalize_canonical_markdown(read_text(markdown_path))
        canonical = _append_missing_urls_from_supplemental_text(canonical, json_text)
    else:
        if json_text:
            warnings.append("MinerU did not produce markdown; canonical text was built from JSON content.")
            canonical = _normalize_canonical_markdown(json_text)
        else:
            text_path = _largest_file(_find_files(raw_dir, [".txt"]))
            if not text_path:
                raise RuntimeError(
                    "MinerU CLI completed but no usable paper text was found. "
                    "Expected a .md file, text-bearing JSON/content list, or .txt output "
                    f"under {raw_dir}."
                )
            warnings.append("MinerU did not produce markdown; canonical text was built from text output.")
            canonical = _normalize_canonical_markdown(read_text(text_path))

    canonical_path = out_dir / "canonical_paper.md"
    extracted_path = out_dir / "extracted_paper.txt"
    write_text(canonical_path, canonical)
    write_text(extracted_path, _markdown_to_plain_text(canonical))
    report = _build_parse_report(
        parser_name="mineru",
        parser_version=version,
        input_pdf=str(pdf_path),
        output_markdown_path=canonical_path,
        output_json_path=json_path,
        num_pages=None,
        text=canonical,
        warnings=warnings,
    )
    report.update(
        {
            "cli_command": cli["name"],
            "output_dir": str(raw_dir),
            "markdown_candidates": [str(path) for path in markdown_candidates],
            "selected_markdown": str(markdown_path) if markdown_path else None,
            "timeout_sec": timeout_sec,
            "timed_out": False,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        }
    )
    return canonical, report


def _validate_pdf_path(pdf_path: Path) -> None:
    if not pdf_path.exists():
        raise RuntimeError(f"PDF file does not exist: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise RuntimeError(f"Expected a .pdf file, got: {pdf_path}")


def _mineru_cli() -> dict[str, str] | None:
    mineru = shutil.which("mineru")
    if mineru:
        return {"name": "mineru", "path": mineru}
    magic_pdf = shutil.which("magic-pdf")
    if magic_pdf:
        return {"name": "magic-pdf", "path": magic_pdf}
    return None


def _mineru_run_command(cli: dict[str, str], pdf_path: Path, output_dir: Path) -> list[str]:
    if cli["name"] == "mineru":
        return [
            cli["path"],
            "-p",
            str(pdf_path),
            "-o",
            str(output_dir),
            "-m",
            "txt",
            "-b",
            "pipeline",
            "-l",
            "en",
            "-f",
            "false",
            "-t",
            "false",
            "--image-analysis",
            "false",
        ]
    return [cli["path"], "-p", str(pdf_path), "-o", str(output_dir)]


def _mineru_timeout_sec() -> int:
    raw = os.environ.get("SURE_PAPER_MINERU_TIMEOUT_SEC")
    if not raw:
        return DEFAULT_MINERU_TIMEOUT_SEC
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MINERU_TIMEOUT_SEC
    return value if value > 0 else DEFAULT_MINERU_TIMEOUT_SEC


def _write_mineru_failure_report(
    *,
    out_dir: Path,
    raw_dir: Path,
    cli: dict[str, str],
    version: str | None,
    pdf_path: Path,
    timeout_sec: int,
    stdout_tail: str,
    stderr_tail: str,
) -> None:
    write_json(
        out_dir / "paper_parse_report.json",
        {
            "enabled": True,
            "status": "fail",
            "parser_name": "mineru",
            "cli_command": cli["name"],
            "parser_version": version,
            "input_pdf": str(pdf_path),
            "output_dir": str(raw_dir),
            "output_markdown_path": str(out_dir / "canonical_paper.md"),
            "output_json_path": None,
            "num_pages": None,
            "extracted_chars": 0,
            "detected_sections": [],
            "has_references": False,
            "markdown_candidates": [],
            "selected_markdown": None,
            "warnings": [f"MinerU parsing timed out after {timeout_sec} seconds."],
            "warning": f"MinerU parsing timed out after {timeout_sec} seconds.",
            "parse_quality_score": 0.0,
            "timeout_sec": timeout_sec,
            "timed_out": True,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        },
    )


def _tail_text(value: str | bytes | None, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = value
    return text[-limit:]


def _command_version(command: str) -> str | None:
    try:
        proc = subprocess.run(
            [command, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    version = (proc.stdout or proc.stderr).strip()
    return version.splitlines()[0] if version else None


def _find_best_file(root: Path, suffixes: list[str]) -> Path | None:
    return _largest_file(_find_files(root, suffixes))


def _find_files(root: Path, suffixes: list[str]) -> list[Path]:
    return sorted(
        [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes],
        key=lambda path: str(path),
    )


def _largest_file(candidates: list[Path]) -> Path | None:
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_size)


def _extract_text_from_mineru_json_candidates(json_candidates: list[Path]) -> str:
    fragments: list[str] = []
    for path in json_candidates:
        try:
            data = json.loads(read_text(path))
        except (OSError, json.JSONDecodeError):
            continue
        _collect_mineru_text_fragments(data, fragments)
    return "\n\n".join(fragment for fragment in fragments if fragment.strip()).strip()


def _append_missing_urls_from_supplemental_text(canonical: str, supplemental_text: str) -> str:
    if not supplemental_text:
        return canonical
    url_pattern = r"https?://(?:www\.)?(?:github\.com|gitlab\.com|huggingface\.co|modelscope\.cn|modelscope\.ai)/[^\s)>\]\"']+"
    existing = {url.rstrip(".,") for url in re.findall(url_pattern, canonical, flags=re.IGNORECASE)}
    missing = []
    for url in re.findall(url_pattern, supplemental_text, flags=re.IGNORECASE):
        clean_url = url.rstrip(".,")
        if clean_url not in existing and clean_url not in missing:
            missing.append(clean_url)
    if not missing:
        return canonical
    return canonical.rstrip() + "\n\n## Extracted Links\n" + "\n".join(missing) + "\n"


def _collect_mineru_text_fragments(data: Any, fragments: list[str]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str) and key.lower() in {"text", "content", "md_content"}:
                fragments.append(value)
            else:
                _collect_mineru_text_fragments(value, fragments)
    elif isinstance(data, list):
        for item in data:
            _collect_mineru_text_fragments(item, fragments)


def _build_parse_report(
    *,
    parser_name: str,
    parser_version: str | None,
    input_pdf: str | None,
    output_markdown_path: Path,
    output_json_path: Path | None,
    num_pages: int | None,
    text: str,
    warnings: list[str],
) -> dict[str, Any]:
    detected_sections = _detect_sections(text)
    has_references = "references" in detected_sections
    quality_score = _parse_quality_score(text, detected_sections, warnings)
    return {
        "enabled": parser_name != "paper_text",
        "status": "pass",
        "parser_name": parser_name,
        "parser_version": parser_version,
        "input_pdf": input_pdf,
        "output_markdown_path": str(output_markdown_path),
        "output_json_path": str(output_json_path) if output_json_path else None,
        "num_pages": num_pages,
        "extracted_chars": len(_markdown_to_plain_text(text)),
        "detected_sections": detected_sections,
        "has_references": has_references,
        "warnings": warnings,
        "warning": "; ".join(warnings) if warnings else None,
        "parse_quality_score": quality_score,
    }


def _detect_sections(text: str) -> list[str]:
    detected: list[str] = []
    for name, pattern in SECTION_PATTERNS:
        heading_match = re.search(rf"(?im)^\s{{0,3}}#*\s*{pattern}\s*$", text)
        body_match = re.search(pattern, text, re.IGNORECASE)
        if (heading_match or body_match) and name not in detected:
            detected.append(name)
    return detected


def _parse_quality_score(text: str, detected_sections: list[str], warnings: list[str]) -> float:
    plain = _markdown_to_plain_text(text)
    score = 1.0
    if len(plain) < MIN_EXTRACTED_PDF_CHARS:
        score -= 0.45
    elif len(plain) < 500:
        score -= 0.2
    if len(detected_sections) < 2:
        score -= 0.25
    if _garbled_ratio(plain) > 0.08:
        score -= 0.25
    if _looks_scanned_or_empty(plain):
        score -= 0.3
    if warnings:
        score -= min(0.2, 0.05 * len(warnings))
    return round(max(0.0, min(1.0, score)), 2)


def _should_try_mineru(text: str, report: dict[str, Any]) -> bool:
    sections = report.get("detected_sections", [])
    plain = _markdown_to_plain_text(text)
    return (
        len(plain) < 200
        or len(sections) < 2
        or _garbled_ratio(plain) > 0.08
        or _looks_scanned_or_empty(plain)
        or float(report.get("parse_quality_score", 0.0)) < 0.65
    )


def _garbled_ratio(text: str) -> float:
    if not text:
        return 1.0
    suspicious = sum(1 for char in text if char == "\ufffd" or (ord(char) < 32 and char not in "\n\t\r"))
    return suspicious / max(1, len(text))


def _looks_scanned_or_empty(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", text)
    return len(text.strip()) < MIN_EXTRACTED_PDF_CHARS or len(words) < 20


def _normalize_canonical_markdown(text: str) -> str:
    normalized = _lightly_normalize_pdf_text(text)
    return normalized.strip() + "\n" if normalized.strip() else ""


def _markdown_to_plain_text(markdown: str) -> str:
    text = re.sub(r"```.*?```", " ", markdown, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _pypdf_version() -> str | None:
    try:
        import pypdf
    except ImportError:
        return None
    return getattr(pypdf, "__version__", None)


def _lightly_normalize_pdf_text(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def load_pdf_text(path: str | Path) -> str:
    """Backward-compatible alias for PDF text extraction."""
    return extract_text_from_pdf(path)


def dotted_get(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur
