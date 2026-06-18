#!/usr/bin/env python3
"""Extract raw table blocks from MinerU content_list JSON output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TABLE_BODY_FIELDS = ("table_body", "content", "html", "text")
CAPTION_FIELDS = ("table_caption", "caption", "table_title")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract table blocks from a MinerU content_list JSON file."
    )
    parser.add_argument(
        "--mineru-dir",
        required=True,
        type=Path,
        help="Directory containing MinerU output files.",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output JSONL path for raw table records.",
    )
    return parser.parse_args()


def load_first_content_list(mineru_dir: Path) -> tuple[Path, list[Any]]:
    matches = sorted(mineru_dir.rglob("*content_list.json"))
    if not matches:
        raise FileNotFoundError(f"No *content_list.json found under {mineru_dir}")

    content_list_path = matches[0]
    with content_list_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected list in {content_list_path}, got {type(data).__name__}")

    return content_list_path, data


def first_present_string(item: dict[str, Any], fields: tuple[str, ...]) -> str:
    values: list[str] = []
    for field in fields:
        value = item.get(field)
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
        else:
            text = json.dumps(value, ensure_ascii=False)
        if text:
            values.append(text)
    return "\n".join(values)


def make_record(
    table_number: int,
    source_file: Path,
    content_index: int,
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "table_id": f"table_{table_number:03d}",
        "source_file": str(source_file),
        "content_index": content_index,
        "page_idx": item.get("page_idx"),
        "bbox": item.get("bbox"),
        "caption": first_present_string(item, CAPTION_FIELDS),
        "raw_table": first_present_string(item, TABLE_BODY_FIELDS),
        "raw_item": item,
    }


def main() -> int:
    args = parse_args()
    content_list_path, content = load_first_content_list(args.mineru_dir)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    table_count = 0
    with args.out.open("w", encoding="utf-8") as f:
        for index, item in enumerate(content):
            if not isinstance(item, dict) or item.get("type") != "table":
                continue
            table_count += 1
            record = make_record(table_count, content_list_path, index, item)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(
        json.dumps(
            {
                "content_list_path": str(content_list_path),
                "tables_raw_path": str(args.out),
                "extracted_table_count": table_count,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
