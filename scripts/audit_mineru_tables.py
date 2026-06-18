#!/usr/bin/env python3
"""Audit MinerU-extracted table records and draft normalized numeric rows."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


KEYWORDS = [
    "WA",
    "UA",
    "WF1",
    "MacroF1",
    "ASR",
    "WER",
    "CER",
    "SER",
    "ACC",
    "Accuracy",
    "UAR",
    "F1",
    "BLEU",
    "DER",
    "cpWER",
    "IEMOCAP",
    "LibriSpeech",
    "AISHELL",
    "MELD",
    "SUPERB",
]

SCORE_METRICS = [
    "WA",
    "UA",
    "WF1",
    "MacroF1",
    "ASR",
    "WER",
    "CER",
    "SER",
    "ACC",
    "Accuracy",
    "UAR",
    "F1",
    "BLEU",
    "DER",
    "cpWER",
]
LOWER_IS_BETTER = {"WER", "CER", "DER", "cpWER"}
HIGHER_IS_BETTER = {"Accuracy", "ACC", "UAR", "F1", "BLEU", "WA", "UA", "WF1", "MacroF1"}
DATASETS = {
    "IEMOCAP",
    "LibriSpeech",
    "AISHELL",
    "MELD",
    "SUPERB",
    "CMU-MOSEI",
    "CMU-MOSI",
    "RAVDESS",
    "SAVEE",
    "M3ED",
    "EmoDB",
    "EMOVO",
    "CaFE",
    "SUBESCO",
    "ShEMO",
    "URDU",
    "AESDD",
    "RESD",
}
CSV_COLUMNS = [
    "paper_id",
    "table_id",
    "page_idx",
    "table_caption",
    "row_index",
    "column_name",
    "row_label",
    "metric",
    "dataset",
    "model",
    "paper_value_raw",
    "paper_value",
    "unit",
    "direction",
    "normalization_status",
    "notes",
]


class HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []
            self._in_cell = True
        elif tag == "br" and self._in_cell and self._cell_parts is not None:
            self._cell_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell_parts is not None:
            cell = normalize_space("".join(self._cell_parts))
            self._row.append(cell)
            self._cell_parts = None
            self._in_cell = False
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(text))).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit MinerU table JSONL output.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            if line.strip():
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError(f"Line {line_number} is not a JSON object")
                records.append(record)
    return records


def detect_format(raw_table: str) -> str:
    stripped = raw_table.lstrip()
    if re.search(r"<\s*table\b", stripped, flags=re.IGNORECASE):
        return "html"
    if "|" in raw_table and re.search(r"^\s*\|?.+\|.+$", raw_table, flags=re.MULTILINE):
        return "markdown"
    return "plain_text"


def table_rows(raw_table: str, detected_format: str) -> list[list[str]]:
    if detected_format == "html":
        parser = HTMLTableParser()
        parser.feed(raw_table)
        return parser.rows
    if detected_format == "markdown":
        rows: list[list[str]] = []
        for line in raw_table.splitlines():
            if "|" not in line or re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", line):
                continue
            rows.append([normalize_space(cell) for cell in line.strip().strip("|").split("|")])
        return rows
    lines = [normalize_space(line) for line in raw_table.splitlines() if line.strip()]
    return [[line] for line in lines]


def markdown_table(rows: list[list[str]], max_rows: int = 20) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows[:max_rows]]
    out = ["| " + " | ".join(padded[0]) + " |"]
    out.append("| " + " | ".join(["---"] * width) + " |")
    for row in padded[1:]:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def text_preview(raw_table: str, rows: list[list[str]], detected_format: str) -> str:
    if rows and detected_format in {"html", "markdown"}:
        preview = markdown_table(rows)
        if preview:
            return preview
    text = normalize_space(re.sub(r"<[^>]+>", " ", raw_table))
    return text[:2000]


def shape(rows: list[list[str]]) -> tuple[int | None, int | None]:
    if not rows:
        return None, None
    return len(rows), max(len(row) for row in rows)


def keyword_hits(text: str) -> list[str]:
    hits: list[str] = []
    for keyword in KEYWORDS:
        pattern = re.escape(keyword)
        if keyword == "F1":
            matched = re.search(pattern, text, re.IGNORECASE)
        else:
            matched = re.search(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", text, re.IGNORECASE)
        if matched:
            hits.append(keyword)
    return hits


def score_metric_hits(text: str) -> list[str]:
    hits: list[str] = []
    for metric in SCORE_METRICS:
        pattern = re.escape(metric)
        if metric == "F1":
            matched = re.search(pattern, text, re.IGNORECASE)
        else:
            matched = re.search(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", text, re.IGNORECASE)
        if matched:
            hits.append(metric)
    return hits


def classify(caption: str, raw_table: str, hits: list[str]) -> str:
    text = f"{caption} {raw_table}".lower()
    score_hits = score_metric_hits(f"{caption} {raw_table}")
    metric_hits = {hit.upper() for hit in hits} & {
        "ASR",
        "WER",
        "CER",
        "SER",
        "ACC",
        "ACCURACY",
        "UAR",
        "F1",
        "BLEU",
        "DER",
        "CPWER",
    }
    if "ablation" in text:
        return "ablation_or_analysis"
    if "performance" in text or score_hits or metric_hits:
        if "pre-training corpus" in text or "self-supervised model" in text:
            return "benchmark_comparison"
        return "experimental_result_candidate"
    if "dataset" in text and any(token in text for token in ["#utts", "#hours", "pretrain", "downstream", "source"]):
        return "dataset_or_setting"
    if any(token in text for token in ["benchmark", "comparison", "superb"]):
        return "benchmark_comparison"
    if any(token in text for token in ["result", "evaluation"]):
        return "experimental_result_candidate"
    return "unknown"


def should_compare(proposed_class: str, hits: list[str]) -> str:
    metric_hits = {hit.upper() for hit in hits} & {
        "WA",
        "UA",
        "WF1",
        "MACROF1",
        "WER",
        "CER",
        "SER",
        "ACC",
        "ACCURACY",
        "UAR",
        "F1",
        "BLEU",
        "DER",
        "CPWER",
    }
    if proposed_class in {"experimental_result_candidate", "benchmark_comparison"} and metric_hits:
        return "yes"
    if proposed_class in {"experimental_result_candidate", "benchmark_comparison", "ablation_or_analysis"}:
        return "review"
    return "no"


def extract_number(value: str) -> tuple[str, str]:
    match = re.search(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", value)
    if not match:
        return "", ""
    raw = match.group(0)
    return raw, raw.replace(",", "")


def infer_metric(column_name: str, row_label: str, caption: str) -> str:
    haystack = f"{column_name} {row_label} {caption}"
    for metric in ["cpWER", "MacroF1", "WF1", "WER", "CER", "DER", "Accuracy", "ACC", "UAR", "F1", "BLEU", "SER", "WA", "UA"]:
        if metric == "F1":
            matched = re.search(re.escape(metric), haystack, re.IGNORECASE)
        else:
            matched = re.search(rf"(?<![A-Za-z0-9]){re.escape(metric)}(?![A-Za-z0-9])", haystack, re.IGNORECASE)
        if matched:
            return metric
    return ""


def infer_dataset(column_name: str, row_label: str, caption: str) -> str:
    haystack = f"{row_label} {column_name} {caption}"
    for dataset in sorted(DATASETS, key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(dataset)}(?![A-Za-z0-9])", haystack, re.IGNORECASE):
            return dataset
    return ""


def infer_direction(metric: str) -> str:
    canonical = "cpWER" if metric.lower() == "cpwer" else metric
    if canonical in LOWER_IS_BETTER:
        return "lower_is_better"
    if canonical in HIGHER_IS_BETTER:
        return "higher_is_better"
    return ""


def csv_rows_for_table(record: dict[str, Any], rows: list[list[str]]) -> list[dict[str, str]]:
    if not rows:
        return []
    headers = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    out: list[dict[str, str]] = []
    caption = normalize_space(record.get("caption", ""))
    for row_index, row in enumerate(body, 1):
        row_label = row[0] if row else ""
        for col_index, cell in enumerate(row[1:], 1):
            number_raw, number = extract_number(cell)
            if not number:
                continue
            column_name = headers[col_index] if col_index < len(headers) else f"column_{col_index + 1}"
            metric = infer_metric(column_name, row_label, caption)
            dataset = infer_dataset(column_name, row_label, caption)
            notes: list[str] = []
            if not metric:
                notes.append("metric not confidently inferred")
            if not dataset:
                notes.append("dataset not confidently inferred")
            out.append(
                {
                    "paper_id": "emotion2vec",
                    "table_id": str(record.get("table_id", "")),
                    "page_idx": "" if record.get("page_idx") is None else str(record.get("page_idx")),
                    "table_caption": caption,
                    "row_index": str(row_index),
                    "column_name": column_name,
                    "row_label": row_label,
                    "metric": metric,
                    "dataset": dataset,
                    "model": "",
                    "paper_value_raw": cell,
                    "paper_value": number,
                    "unit": "",
                    "direction": infer_direction(metric),
                    "normalization_status": "draft",
                    "notes": "; ".join(notes),
                }
            )
    return out


def write_preview(path: Path, audit: dict[str, Any]) -> None:
    path.write_text(
        "\n".join(
            [
                f"# {audit['table_id']}",
                "",
                f"- page_idx: {audit['page_idx']}",
                f"- caption: {audit['caption']}",
                f"- detected_format: {audit['detected_format']}",
                f"- estimated_shape: {audit['estimated_shape']}",
                f"- keyword_hits: {', '.join(audit['keyword_hits']) if audit['keyword_hits'] else ''}",
                f"- proposed_class: {audit['proposed_class']}",
                f"- should_compare_later: {audit['should_compare_later']}",
                "",
                "## Raw Preview",
                "",
                audit["raw_preview"],
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    records = load_jsonl(args.input)
    preview_dir = args.out_dir / "tables_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)

    audits: list[dict[str, Any]] = []
    csv_rows: list[dict[str, str]] = []
    candidate_ids: list[str] = []
    review_ids: list[str] = []

    for record in records:
        raw_table = str(record.get("raw_table", ""))
        caption = normalize_space(record.get("caption", ""))
        detected = detect_format(raw_table)
        rows = table_rows(raw_table, detected)
        row_count, col_count = shape(rows)
        hits = keyword_hits(f"{caption} {raw_table}")
        proposed_class = classify(caption, raw_table, hits)
        compare_later = should_compare(proposed_class, hits)
        if compare_later == "yes":
            candidate_ids.append(str(record.get("table_id", "")))
        elif compare_later == "review":
            review_ids.append(str(record.get("table_id", "")))
        audit = {
            "table_id": str(record.get("table_id", "")),
            "page_idx": record.get("page_idx"),
            "caption": caption,
            "detected_format": detected,
            "estimated_shape": f"{row_count or 'unknown'} rows x {col_count or 'unknown'} columns",
            "keyword_hits": hits,
            "proposed_class": proposed_class,
            "should_compare_later": compare_later,
            "raw_preview": text_preview(raw_table, rows, detected),
        }
        audits.append(audit)
        write_preview(preview_dir / f"{audit['table_id']}.md", audit)
        csv_rows.extend(csv_rows_for_table(record, rows))

    audit_md = args.out_dir / "table_audit.md"
    with audit_md.open("w", encoding="utf-8") as f:
        f.write("# MinerU Table Audit\n\n")
        for audit in audits:
            f.write(f"## {audit['table_id']}\n\n")
            f.write(f"- page_idx: {audit['page_idx']}\n")
            f.write(f"- caption: {audit['caption']}\n")
            f.write(f"- detected_format: {audit['detected_format']}\n")
            f.write(f"- estimated_shape: {audit['estimated_shape']}\n")
            f.write(
                f"- keyword_hits: {', '.join(audit['keyword_hits']) if audit['keyword_hits'] else ''}\n"
            )
            f.write(f"- proposed_class: {audit['proposed_class']}\n")
            f.write(f"- should_compare_later: {audit['should_compare_later']}\n\n")
            f.write("### Raw Preview\n\n")
            f.write(audit["raw_preview"])
            f.write("\n\n")

    normalized_csv = args.out_dir / "tables_normalized_draft.csv"
    with normalized_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(csv_rows)

    manifest = {
        "status": "success",
        "input_tables_raw": str(args.input),
        "table_count": len(records),
        "audit_md": str(audit_md),
        "preview_dir": str(preview_dir),
        "normalized_draft_csv": str(normalized_csv),
        "candidate_result_tables": candidate_ids,
        "needs_human_review": True,
        "notes": [
            "Classification and normalization are heuristic drafts for human review.",
            f"Tables needing review: {', '.join(review_ids) if review_ids else 'none'}",
            f"Normalized numeric draft rows: {len(csv_rows)}",
        ],
    }
    manifest_path = args.out_dir / "table_normalization_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
