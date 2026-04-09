#!/usr/bin/env python3
"""Refresh deterministic report snapshots from the checked-in report sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sure_eval.core.logging import configure_logging, get_logger
from sure_eval.reports import ReportManager, SOTAManager

configure_logging(level="INFO")
logger = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh report snapshots")
    parser.add_argument("--markdown", type=str, default="reports/model_report.md", help="Markdown output path")
    parser.add_argument("--json", type=str, help="Optional JSON summary output")
    args = parser.parse_args()

    report_manager = ReportManager()
    sota_manager = SOTAManager()

    markdown = report_manager.generate_markdown_report(args.markdown)
    summary = {
        "num_models": len(report_manager.list_models()),
        "num_sota_datasets": len(sota_manager.list_datasets()),
        "markdown_path": str(Path(args.markdown)),
    }

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if args.json:
        output_path = Path(args.json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Wrote report summary", path=str(output_path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
