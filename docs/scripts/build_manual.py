#!/usr/bin/env python3
"""Build SURE-EVAL user manual HTML and PDF from Markdown.

Usage:
    python docs/scripts/build_manual.py

Dependencies:
    pip install markdown weasyprint

System dependencies for WeasyPrint (Debian/Ubuntu example):
    sudo apt-get install -y libcairo2-dev libpango1.0-dev libgdk-pixbuf2.0-dev \
        libffi-dev shared-mime-info fonts-noto-cjk
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import markdown
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Python 'markdown' package is required. Install with: pip install markdown"
    ) from exc

try:
    from weasyprint import HTML, CSS
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Python 'weasyprint' package is required. Install with: pip install weasyprint"
    ) from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "assets"
MD_PATH = DOCS_DIR / "SURE-EVAL_User_Manual.md"
HTML_PATH = DOCS_DIR / "SURE-EVAL_User_Manual.html"
PDF_PATH = DOCS_DIR / "SURE-EVAL_User_Manual.pdf"
CSS_PATH = ASSETS_DIR / "manual.css"


def build_html(md_path: Path, css_path: Path) -> str:
    """Return full HTML document string."""
    md_text = md_path.read_text(encoding="utf-8")

    # Python-Markdown with table/code/TOC support
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc"],
        output_format="xhtml",
    )
    body = md.convert(md_text)
    toc_html = md.toc if hasattr(md, "toc") else ""

    css_href = css_path.relative_to(DOCS_DIR).as_posix()

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>SURE-EVAL 用户使用手册</title>
<link rel="stylesheet" href="{css_href}">
</head>
<body>
{body}
</body>
</html>
"""


def write_outputs(html_doc: str, html_path: Path, pdf_path: Path, css_path: Path) -> None:
    """Write HTML and PDF files."""
    html_path.write_text(html_doc, encoding="utf-8")
    print(f"[build_manual] Wrote {html_path}")

    html = HTML(filename=str(html_path), base_url=str(DOCS_DIR))
    css = CSS(filename=str(css_path))
    html.write_pdf(str(pdf_path), stylesheets=[css])
    print(f"[build_manual] Wrote {pdf_path}")


def validate_outputs(html_path: Path, pdf_path: Path) -> None:
    """Sanity-check generated artifacts."""
    errors: list[str] = []

    if not html_path.exists() or html_path.stat().st_size < 1024:
        errors.append(f"HTML output missing or too small: {html_path}")

    if not pdf_path.exists() or pdf_path.stat().st_size < 1024:
        errors.append(f"PDF output missing or too small: {pdf_path}")

    html_text = html_path.read_text(encoding="utf-8")
    required_snippets = [
        "SURE-EVAL",
        "用户使用手册",
        "docs/agents/main_flow_agent",
        "docs/agents/model_tool_agent",
    ]
    for snippet in required_snippets:
        if snippet not in html_text:
            errors.append(f"HTML missing expected snippet: {snippet!r}")

    if errors:
        print("[build_manual] Validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        raise SystemExit(1)

    print("[build_manual] Validation passed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SURE-EVAL user manual")
    parser.add_argument(
        "--md",
        type=Path,
        default=MD_PATH,
        help="Path to the Markdown source",
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=HTML_PATH,
        help="Path for the generated HTML output",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=PDF_PATH,
        help="Path for the generated PDF output",
    )
    parser.add_argument(
        "--css",
        type=Path,
        default=CSS_PATH,
        help="Path to the CSS stylesheet",
    )
    parser.add_argument(
        "--skip-pdf",
        action="store_true",
        help="Only generate HTML, skip PDF",
    )
    args = parser.parse_args(argv)

    if not args.md.exists():
        print(f"[build_manual] Markdown source not found: {args.md}", file=sys.stderr)
        return 1

    if not args.css.exists():
        print(f"[build_manual] CSS not found: {args.css}", file=sys.stderr)
        return 1

    html_doc = build_html(args.md, args.css)
    args.html.write_text(html_doc, encoding="utf-8")
    print(f"[build_manual] Wrote {args.html}")

    if not args.skip_pdf:
        html = HTML(filename=str(args.html), base_url=str(args.html.parent))
        css = CSS(filename=str(args.css))
        html.write_pdf(str(args.pdf), stylesheets=[css])
        print(f"[build_manual] Wrote {args.pdf}")

    validate_outputs(args.html, args.pdf if not args.skip_pdf else args.html.with_suffix(".pdf"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
