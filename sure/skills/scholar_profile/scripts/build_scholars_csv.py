#!/usr/bin/env python3
"""
Generate scholars.csv from a list of scholar names.

Input: Text file, one name per line.
Output: CSV file with columns: author, dblp_url, sources_file.

Usage:
  python build_scholars_csv.py --input names.txt --output scholars.csv [--sources-dir PINN]
"""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import requests


def safe_name(author: str) -> str:
    """Convert author name to a safe filename."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", (author or "").strip())
    name = name.strip("_")
    return name or "unknown_author"


def search_dblp_author(name: str, proxies: dict = None) -> Tuple[str, str]:
    """Search DBLP for an author, return (author_name, dblp_url)."""
    url = "https://dblp.org/search/author/api"
    params = {"q": name, "format": "json", "h": 5}
    try:
        resp = requests.get(url, params=params, timeout=20, proxies=proxies)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [WARN] DBLP search failed for '{name}': {exc}")
        return "", ""

    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    if not hits:
        print(f"  [WARN] No DBLP result for '{name}'")
        return "", ""

    # Exact match first
    for h in hits:
        info = h.get("info", {})
        author_name = (info.get("author") or "").strip()
        if author_name.lower() == name.lower():
            dblp_url = (info.get("url") or "").strip()
            return author_name, dblp_url

    # No exact match, take first result
    info = hits[0].get("info", {})
    author_name = (info.get("author") or "").strip()
    dblp_url = (info.get("url") or "").strip()
    print(f"  [WARN] No exact match for '{name}', using first result: '{author_name}'")
    return author_name, dblp_url


def main():
    parser = argparse.ArgumentParser(
        description="Generate scholars.csv from a list of scholar names."
    )
    parser.add_argument("--input", required=True, help="Scholar name list file, one name per line")
    parser.add_argument("--output", default="scholars.csv", help="Output CSV file path")
    parser.add_argument("--sources-dir", default="", help="Sources file directory (default: same as CSV)")
    parser.add_argument("--http-proxy", default="", help="HTTP proxy")
    parser.add_argument("--https-proxy", default="", help="HTTPS proxy")
    parser.add_argument("--skip-existing-csv", default="false", help="Skip scholars already in CSV (true/false)")

    args = parser.parse_args()

    # Proxy (set via environment variables, requests will read automatically)
    if args.http_proxy:
        os.environ["HTTP_PROXY"] = args.http_proxy.strip()
    if args.https_proxy:
        os.environ["HTTPS_PROXY"] = args.https_proxy.strip()
    proxies = None  # Let requests read from environment variables

    # Read name list
    input_path = args.input.strip()
    if not os.path.exists(input_path):
        print(f"Input file not found: {input_path}")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        names = [line.strip() for line in f if line.strip()]

    if not names:
        print("No names found in input file")
        sys.exit(1)

    # Determine sources directory
    sources_dir = args.sources_dir.strip()
    if not sources_dir:
        sources_dir = os.path.dirname(args.output) or "."

    # Existing CSV scholars (for deduplication)
    existing = set()
    output_path = args.output.strip()
    skip_existing = str(args.skip_existing_csv).strip().lower() in {"1", "true", "yes", "y", "on"}
    if skip_existing and os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing.add((row.get("author") or "").strip().lower())

    # Search DBLP and write CSV
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    rows = []
    ok_count = 0
    fail_count = 0

    print(f"[INFO] Processing {len(names)} scholars...")

    for i, name in enumerate(names, 1):
        if existing and name.lower() in existing:
            print(f"[{i}/{len(names)}] SKIP {name} (already in CSV)")
            continue

        print(f"[{i}/{len(names)}] Searching DBLP for '{name}'...")
        author_name, dblp_url = search_dblp_author(name, proxies)

        if not dblp_url:
            fail_count += 1
            continue

        safe = safe_name(author_name or name)
        sources_file = f"{sources_dir}/{safe}_sources.txt"

        rows.append({
            "author": author_name or name,
            "dblp_url": dblp_url,
            "sources_file": sources_file,
        })
        ok_count += 1
        print(f"  -> {author_name} | {dblp_url}")
        time.sleep(2.0)  # DBLP rate limiting

    # Write CSV
    if rows:
        file_exists = os.path.exists(output_path) and skip_existing
        mode = "a" if file_exists else "w"
        with open(output_path, mode, encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["author", "dblp_url", "sources_file"])
            if mode == "w":
                writer.writeheader()
            writer.writerows(rows)

    print(f"\n[SUMMARY] Total: {len(names)}, OK: {ok_count}, Failed: {fail_count}")
    print(f"CSV: {output_path}")
    print(f"\nNext step:")
    print(f"  export TAVILY_API_KEY=...")
    print(f"  export OPENAI_API_KEY=...")
    print(f"  python run_batch_parallel.py --csv {output_path}")


# ============ Callable interface for pipeline ============

def run_single(scholar_name: str, output_dir: str, native_name: str = None) -> str:
    """Run DBLP search for a single scholar and write CSV.

    Args:
        scholar_name: Full name of the scholar.
        output_dir: Output directory path.
        native_name: Optional native-language name (unused here, kept for API consistency).

    Returns:
        Path to the generated CSV file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "scholars.csv"
    sources_dir = str(out)

    author_name, dblp_url = search_dblp_author(scholar_name)
    if not dblp_url:
        raise RuntimeError(f"DBLP search returned no results for '{scholar_name}'.")

    safe = safe_name(author_name or scholar_name)
    sources_file = f"{sources_dir}/{safe}_sources.txt"

    rows = [{
        "author": author_name or scholar_name,
        "dblp_url": dblp_url,
        "sources_file": sources_file,
    }]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["author", "dblp_url", "sources_file"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[build_scholars_csv] {author_name} | {dblp_url} -> {csv_path}")
    return str(csv_path)


if __name__ == "__main__":
    main()
