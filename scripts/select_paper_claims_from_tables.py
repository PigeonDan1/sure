#!/usr/bin/env python3
"""Select paper claim candidates from MinerU raw table evidence.

The script is query-driven and intentionally case-agnostic. Paper-, model-,
dataset-, task-, and metric-specific terms belong in the query JSON.
"""

import argparse
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path


NUMERIC_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


def normalize_space(text):
    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()


def base_key(text):
    text = normalize_space(text).lower()
    text = re.sub(r"[%↑↓]", " ", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def alias_lookup(query, category):
    aliases = (((query.get("key_aliases") or {}).get(category)) or {})
    out = {}
    for canonical, values in aliases.items():
        canonical_norm = base_key(canonical)
        out[canonical_norm] = canonical_norm
        if isinstance(values, str):
            values = [values]
        for value in values or []:
            out[base_key(value)] = canonical_norm
    return out


def normalize_key(text, query, category=None):
    normalized = base_key(text)
    if category:
        return alias_lookup(query, category).get(normalized, normalized)
    return normalized


def normalize_targets(values, query, category):
    targets = []
    for value in values or []:
        norm = normalize_key(value, query, category)
        if norm and norm not in targets:
            targets.append(norm)
    return targets


def parse_number(text):
    cleaned = normalize_space(text).replace(",", "")
    if re.search(r"[A-Za-z]", cleaned):
        return None
    match = NUMERIC_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path):
    records = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            if line.strip():
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("Line %s is not a JSON object" % line_number)
                records.append(record)
    return records


def write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def validate_query(query):
    required = ["paper_id", "paper_title", "source_pdf", "mineru_content_list", "tables_raw"]
    missing = [key for key in required if not query.get(key)]
    if missing:
        raise ValueError("Query is missing required fields: %s" % ", ".join(missing))
    if not isinstance(query.get("validation_policy") or {}, dict):
        raise ValueError("Query validation_policy must be an object")


class Cell(object):
    def __init__(self, text, row, col, rowspan=1, colspan=1, is_header=False, source_row=None, source_col=None):
        self.text = normalize_space(text)
        self.row = row
        self.col = col
        self.rowspan = rowspan
        self.colspan = colspan
        self.is_header = is_header
        self.source_row = row if source_row is None else source_row
        self.source_col = col if source_col is None else source_col


class HTMLTableParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.rows = []
        self._row = None
        self._cell = None
        self._cell_parts = None
        self._row_index = -1
        self._cell_attrs = {}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row_index += 1
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            attr_map = dict(attrs)
            self._cell_attrs = attr_map
            self._cell_parts = []
            self._cell = tag
        elif tag == "br" and self._cell_parts is not None:
            self._cell_parts.append(" ")

    def handle_data(self, data):
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._row is not None and self._cell_parts is not None:
            text = normalize_space("".join(self._cell_parts))
            rowspan = _safe_int(self._cell_attrs.get("rowspan"), 1)
            colspan = _safe_int(self._cell_attrs.get("colspan"), 1)
            self._row.append({"text": text, "rowspan": rowspan, "colspan": colspan, "is_header": tag == "th"})
            self._cell_parts = None
            self._cell = None
            self._cell_attrs = {}
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _safe_int(value, default):
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def detect_format(raw_table):
    stripped = (raw_table or "").lstrip()
    if re.search(r"<\s*table\b", stripped, flags=re.IGNORECASE):
        return "html"
    if "|" in (raw_table or "") and re.search(r"^\s*\|?.+\|.+$", raw_table or "", flags=re.MULTILINE):
        return "markdown"
    return "plain_text"


def parse_html_grid(raw_table):
    parser = HTMLTableParser()
    parser.feed(raw_table or "")
    grid = []
    occupied = {}
    for r, row in enumerate(parser.rows):
        if len(grid) <= r:
            grid.append([])
        c = 0
        for source in row:
            while occupied.get((r, c)) is not None:
                c += 1
            cell = Cell(source.get("text"), r, c, source.get("rowspan", 1), source.get("colspan", 1), source.get("is_header", False))
            for rr in range(r, r + cell.rowspan):
                while len(grid) <= rr:
                    grid.append([])
                for cc in range(c, c + cell.colspan):
                    while len(grid[rr]) <= cc:
                        grid[rr].append(None)
                    clone = Cell(cell.text, rr, cc, 1, 1, cell.is_header, cell.row, cell.col)
                    grid[rr][cc] = clone
                    if rr != r or cc != c:
                        occupied[(rr, cc)] = clone
            c += cell.colspan
    return pad_grid(grid), []


def parse_markdown_grid(raw_table):
    rows = []
    warnings = []
    for line in (raw_table or "").splitlines():
        if "|" not in line:
            continue
        if re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", line):
            continue
        rows.append([normalize_space(cell) for cell in line.strip().strip("|").split("|")])
    if not rows:
        warnings.append("markdown_parser_no_rows")
        return [], warnings
    width = max(len(row) for row in rows)
    grid = []
    for r, row in enumerate(rows):
        grid_row = []
        for c in range(width):
            text = row[c] if c < len(row) else ""
            grid_row.append(Cell(text, r, c))
        grid.append(grid_row)
    warnings.append("markdown_parser_no_span_support")
    return grid, warnings


def pad_grid(grid):
    if not grid:
        return []
    width = max(len(row) for row in grid)
    for r, row in enumerate(grid):
        while len(row) < width:
            row.append(Cell("", r, len(row)))
    return grid


def parse_table_grid(raw_table):
    fmt = detect_format(raw_table)
    if fmt == "html":
        grid, warnings = parse_html_grid(raw_table)
        return grid, fmt, warnings
    if fmt == "markdown":
        grid, warnings = parse_markdown_grid(raw_table)
        return grid, fmt, warnings
    return [], fmt, ["plain_text_fallback_no_structured_claims"]


def table_in_scope(record, query):
    scope = query.get("table_scope") or {}
    table_ids = scope.get("target_table_ids") or []
    pages = scope.get("page_indices") or []
    keywords = scope.get("caption_keywords") or []
    if table_ids:
        return record.get("table_id") in table_ids
    checks = []
    if pages:
        checks.append(record.get("page_idx") in pages)
    if keywords:
        text = ("%s %s" % (record.get("caption") or "", record.get("raw_table") or "")).lower()
        checks.append(all(str(keyword).lower() in text for keyword in keywords))
    if not checks:
        return True
    return any(checks)


def is_target_body_row(grid, row_index, query):
    target_rows = normalize_targets(query.get("target_rows"), query, "rows")
    target_models = normalize_targets(query.get("target_models"), query, "models")
    targets = set(target_rows + target_models)
    if not targets or row_index >= len(grid):
        return False
    for cell in grid[row_index]:
        text = normalize_space(cell.text if cell else "")
        if not text:
            continue
        if normalize_key(text, query, "rows") in targets or normalize_key(text, query, "models") in targets:
            return True
    return False


def detect_header_rows(grid, query):
    header_rows = []
    for r, row in enumerate(grid):
        numeric = sum(1 for cell in row if cell and parse_number(cell.text) is not None)
        nonempty = sum(1 for cell in row if cell and normalize_space(cell.text))
        if nonempty and numeric < max(1, nonempty // 2):
            header_rows.append(r)
        else:
            break
    return header_rows


def unique_texts(cells):
    values = []
    seen = set()
    for cell in cells:
        text = normalize_space(cell.text if cell else "")
        if text and text not in seen:
            seen.add(text)
            values.append(text)
    return values


def row_header_texts(cells):
    values = []
    seen = set()
    for cell in cells:
        text = normalize_space(cell.text if cell else "")
        if not text:
            continue
        if parse_number(text) is not None:
            continue
        if text not in seen:
            seen.add(text)
            values.append(text)
    return values


def match_from_tokens(tokens, targets, query, category):
    if not targets:
        return None, None
    for token in tokens:
        normalized = normalize_key(token, query, category)
        if normalized in targets:
            return token, normalized
    return None, None


def build_cell_evidence(records, query):
    evidence = []
    parsed_tables = {}
    for record in records:
        if not table_in_scope(record, query):
            continue
        raw_table = record.get("raw_table") or ""
        raw_hash = hashlib.sha256(raw_table.encode("utf-8")).hexdigest()
        grid, fmt, warnings = parse_table_grid(raw_table)
        parsed_tables[record.get("table_id")] = {
            "record": record,
            "grid": grid,
            "format": fmt,
            "warnings": warnings,
            "header_rows": detect_header_rows(grid, query) if grid else [],
            "raw_hash": raw_hash,
        }
        if not grid:
            evidence.append({
                "paper_id": query.get("paper_id"),
                "table_id": record.get("table_id"),
                "table_caption": record.get("caption") or "",
                "page_idx": record.get("page_idx"),
                "row_index": 0,
                "col_index": 0,
                "cell_text": normalize_space(raw_table)[:2000],
                "cell_value_raw": None,
                "cell_value": None,
                "row_header_path": [],
                "col_header_path": [],
                "header_path": [],
                "row_key_candidates": [],
                "column_key_candidates": [],
                "metric_key_candidates": [],
                "dataset_key_candidates": [],
                "model_key_candidates": [],
                "normalized_tokens": [],
                "evidence_source": "raw_table:%s" % fmt,
                "source_raw_table_hash": raw_hash,
                "notes": warnings,
            })
            continue
        header_rows = parsed_tables[record.get("table_id")]["header_rows"]
        for r, row in enumerate(grid):
            for c, cell in enumerate(row):
                text = normalize_space(cell.text if cell else "")
                if not text:
                    continue
                value = parse_number(text)
                row_headers = row_header_texts(grid[r][:c])
                col_headers = unique_texts([grid[hr][c] for hr in header_rows if hr < len(grid) and c < len(grid[hr])])
                header_path = row_headers + col_headers
                normalized_tokens = []
                for token in [text] + header_path:
                    norm = base_key(token)
                    if norm and norm not in normalized_tokens:
                        normalized_tokens.append(norm)
                evidence.append({
                    "paper_id": query.get("paper_id"),
                    "table_id": record.get("table_id"),
                    "table_caption": record.get("caption") or "",
                    "page_idx": record.get("page_idx"),
                    "row_index": r,
                    "col_index": c,
                    "cell_text": text,
                    "cell_value_raw": text if value is not None else None,
                    "cell_value": value,
                    "row_header_path": row_headers,
                    "col_header_path": col_headers,
                    "header_path": header_path,
                    "row_key_candidates": row_headers,
                    "column_key_candidates": col_headers,
                    "metric_key_candidates": col_headers,
                    "dataset_key_candidates": col_headers,
                    "model_key_candidates": row_headers,
                    "normalized_tokens": normalized_tokens,
                    "evidence_source": "raw_table:%s" % fmt,
                    "source_raw_table_hash": raw_hash,
                    "notes": list(warnings),
                })
    return evidence, parsed_tables


def select_claims(cell_evidence, query):
    target_rows = normalize_targets(query.get("target_rows"), query, "rows")
    target_models = normalize_targets(query.get("target_models"), query, "models")
    target_datasets = normalize_targets(query.get("target_datasets"), query, "datasets")
    target_metrics = normalize_targets(query.get("target_metrics"), query, "metrics")
    target_columns = [base_key(value) for value in query.get("target_columns") or []]
    metric_directions = {}
    for metric, direction in (query.get("metric_directions") or {}).items():
        metric_directions[normalize_key(metric, query, "metrics")] = direction

    candidates = []
    for cell in cell_evidence:
        if cell.get("cell_value") is None:
            continue
        tokens = [cell.get("cell_text") or ""] + cell.get("header_path", [])
        row_tokens = cell.get("row_header_path", [])
        col_tokens = cell.get("col_header_path", [])

        metric_raw, metric_norm = match_from_tokens(col_tokens + tokens, target_metrics, query, "metrics")
        if target_metrics and not metric_norm:
            continue
        dataset_raw, dataset_norm = match_from_tokens(col_tokens + tokens, target_datasets, query, "datasets")
        if target_datasets and not dataset_norm:
            continue
        model_raw, model_norm = match_from_tokens(row_tokens + tokens, target_models, query, "models")
        row_raw, row_norm = match_from_tokens(row_tokens + tokens, target_rows, query, "rows")
        if target_models and not model_norm:
            continue
        if target_rows and not row_norm:
            continue
        column_raw = " / ".join(col_tokens) if col_tokens else None
        column_norm = base_key(column_raw or "")
        if target_columns and column_norm not in target_columns:
            continue

        direction = metric_directions.get(metric_norm)
        key_status = {
            "table_match": True,
            "row_key_match": bool(row_norm or not target_rows),
            "model_key_match": bool(model_norm or not target_models),
            "dataset_key_match": bool(dataset_norm or not target_datasets),
            "metric_key_match": bool(metric_norm or not target_metrics),
            "value_parse_success": True,
            "metric_direction_available": bool(direction),
            "sources": {
                "row_key": "row_header_path",
                "model_key": "row_header_path",
                "dataset_key": "col_header_path",
                "metric_key": "col_header_path",
                "cell_coordinate": [cell.get("row_index"), cell.get("col_index")],
            },
        }
        header_path = cell.get("header_path", [])
        candidates.append({
            "paper_id": query.get("paper_id"),
            "paper_title": query.get("paper_title"),
            "source_pdf": query.get("source_pdf"),
            "mineru_content_list": query.get("mineru_content_list"),
            "table_id": cell.get("table_id"),
            "table_caption": cell.get("table_caption"),
            "table_page_idx": cell.get("page_idx"),
            "row_key": row_raw or model_raw,
            "row_key_normalized": row_norm or model_norm,
            "column_key": column_raw,
            "column_key_normalized": column_norm,
            "model_key": model_raw,
            "model_key_normalized": model_norm,
            "dataset_key": dataset_raw,
            "dataset_key_normalized": dataset_norm,
            "metric_key": metric_raw,
            "metric_key_normalized": metric_norm,
            "header_path": header_path,
            "header_path_normalized": [base_key(token) for token in header_path],
            "paper_value": cell.get("cell_value"),
            "paper_value_raw": cell.get("cell_value_raw"),
            "paper_value_unit": query.get("paper_value_unit"),
            "metric_direction": direction,
            "task": query.get("task"),
            "split": query.get("split"),
            "evidence_source": cell.get("evidence_source"),
            "evidence_text": "%s = %s" % (" / ".join(header_path), cell.get("cell_text")),
            "evidence_cell": {
                "table_id": cell.get("table_id"),
                "row_index": cell.get("row_index"),
                "col_index": cell.get("col_index"),
                "cell_text": cell.get("cell_text"),
            },
            "extraction_method": "schema_first_key_matching_from_raw_table",
            "key_match_status": key_status,
            "validation_status": "candidate",
            "validation_method": "not_validated",
            "confidence": "unknown",
            "needs_human_review": False,
            "notes": list(cell.get("notes") or []),
        })
    return candidates


def missing_expected_keys(candidates, query):
    missing = []
    expected = query.get("expected_claim_count")
    if expected is not None and len(candidates) < expected:
        missing.append("expected_claim_count:%s actual:%s" % (expected, len(candidates)))
    seen_metrics = set(candidate.get("metric_key_normalized") for candidate in candidates)
    for metric in normalize_targets(query.get("target_metrics"), query, "metrics"):
        if metric not in seen_metrics:
            missing.append("metric:%s" % metric)
    return missing


def run_selection(query_path, out_dir, legacy_selection=None):
    query = load_json(query_path)
    validate_query(query)
    tables = load_jsonl(query["tables_raw"])
    selected = [record for record in tables if table_in_scope(record, query)]
    cell_evidence, parsed_tables = build_cell_evidence(tables, query)
    candidates = select_claims(cell_evidence, query)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = out_dir / "table_cell_evidence.jsonl"
    candidates_path = out_dir / "paper_claim_candidates.jsonl"
    manifest_path = out_dir / "claim_selection_manifest.json"
    write_jsonl(evidence_path, cell_evidence)
    write_jsonl(candidates_path, candidates)

    warnings = []
    for table in parsed_tables.values():
        warnings.extend(table.get("warnings") or [])
    manifest = {
        "status": "success",
        "query_path": str(query_path),
        "tables_raw_path": query.get("tables_raw"),
        "source_pdf": query.get("source_pdf"),
        "selected_tables": [record.get("table_id") for record in selected],
        "total_tables_seen": len(tables),
        "total_cell_evidence": len(cell_evidence),
        "candidate_claim_count": len(candidates),
        "expected_claim_count": query.get("expected_claim_count"),
        "missing_expected_keys": missing_expected_keys(candidates, query),
        "legacy_selection_used_as_input": False,
        "warnings": sorted(set(warnings)),
        "notes": [
            "Legacy selection path was accepted only for post-run sanity context." if legacy_selection else "No legacy selection path provided.",
            "Claim candidates are selected from tables_raw evidence using query keys.",
        ],
    }
    if manifest["missing_expected_keys"]:
        manifest["status"] = "needs_review"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    return {
        "manifest": manifest,
        "table_cell_evidence": str(evidence_path),
        "paper_claim_candidates": str(candidates_path),
        "claim_selection_manifest": str(manifest_path),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Select paper claim candidates from raw MinerU tables.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--legacy-selection")
    return parser.parse_args()


def main():
    args = parse_args()
    result = run_selection(args.query, args.out_dir, args.legacy_selection)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
