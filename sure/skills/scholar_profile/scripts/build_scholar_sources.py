#!/usr/bin/env python3
"""
Scholar resource auto-discovery script.
Given a scholar name and DBLP URL, collect high-quality resource links and output a standard sources.txt file.

Data sources:
  - DBLP: institution info, personal homepage
  - OpenAlex: homepage_url, last_known_institutions
  - Google Scholar: author profile (via scholarly)
  - Wikipedia: author page
  - Tavily Search API: interviews, lab homepages, news
"""

import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote_plus, urlparse

import requests

from utils import (
    DEFAULT_HEADERS,
    DEFAULT_TIMEOUT,
    extract_dblp_pid,
    setup_logging,
)

logger = setup_logging(__name__)

# ============ Data classes ============

@dataclass
class ScholarSources:
    """Scholar resource collection."""
    author_name: str = ""
    dblp_url: str = ""
    homepage_urls: List[str] = field(default_factory=list)
    google_scholar_url: str = ""
    wikipedia_url: str = ""
    news_interview_urls: List[Tuple[str, str]] = field(default_factory=list)  # (label, url)
    lab_urls: List[str] = field(default_factory=list)
    institution_urls: List[str] = field(default_factory=list)
    extra_urls: List[Tuple[str, str]] = field(default_factory=list)  # (label, url)


# ============ HTTP utilities ============

def request_get(url: str, timeout: int = DEFAULT_TIMEOUT, **kwargs) -> Optional[requests.Response]:
    kwargs.setdefault("timeout", timeout)
    try:
        resp = requests.get(url, **kwargs)
        resp.raise_for_status()
        return resp
    except Exception as exc:
        logger.debug(f"request_get failed for {url}: {type(exc).__name__}: {exc}")
        return None


# ============ DBLP ============

def fetch_dblp_info(dblp_url: str) -> Tuple[str, List[str]]:
    """Fetch author name and possible homepage links from DBLP."""
    pid = extract_dblp_pid(dblp_url)
    if not pid:
        return "", []

    xml_url = f"https://dblp.org/pid/{pid}.xml"
    resp = request_get(xml_url, proxies=_get_proxies())
    if not resp:
        return "", []

    try:
        root = ET.fromstring(resp.text)
    except Exception:
        return "", []

    author_name = (root.attrib.get("name") or "").strip()

    # DBLP person node may have url (homepage) and author elements
    homepages = []
    person = root.find("person")
    if person is not None:
        for url_elem in person.findall("url"):
            u = (url_elem.text or "").strip()
            if u:
                homepages.append(u)
        for author_elem in person.findall("author"):
            note = author_elem.find("note")
            if note is not None and note.text:
                text = note.text.strip()
                if text.startswith("http"):
                    homepages.append(text)

    return author_name, homepages


# ============ OpenAlex ============

def fetch_openalex_info(author_name: str) -> Tuple[str, List[str]]:
    """Fetch homepage_url and institution pages from OpenAlex."""
    if not author_name:
        return "", []

    url = "https://api.openalex.org/authors"
    params = {
        "search": author_name,
        "per-page": 3,
    }
    resp = request_get(url, params=params, proxies=_get_proxies())
    if not resp:
        return "", []

    try:
        results = resp.json().get("results", [])
    except Exception:
        return "", []

    if not results:
        return "", []

    # Take best match
    best = None
    for r in results:
        display = (r.get("display_name") or "").strip()
        if display.lower() == author_name.lower():
            best = r
            break
    if not best:
        best = results[0]

    homepage = (best.get("homepage_url") or "").strip()

    institutions = []
    institution_names = []
    for inst in best.get("last_known_institutions", []):
        inst_url = (inst.get("homepage_url") or "").strip()
        if inst_url:
            institutions.append(inst_url)
        inst_name = (inst.get("display_name") or "").strip()
        if inst_name:
            institution_names.append(inst_name)

    # Infer homepage URL from institution name
    for inst_name in institution_names:
        if not inst_name:
            continue
        slug = inst_name.lower().replace("university of ", "").replace(" ", "")
        if "california" in inst_name.lower() and "riverside" in inst_name.lower():
            institutions.append("https://www.ucr.edu/")
        elif "colorado" in inst_name.lower() and "boulder" in inst_name.lower():
            institutions.append("https://www.colorado.edu/")

    return homepage, institutions


# ============ Google Scholar ============

def find_google_scholar_url(author_name: str) -> str:
    """Find Google Scholar profile URL using scholarly library, fallback to search link."""
    try:
        from scholarly import scholarly
        search_query = scholarly.search_author(author_name)
        author = next(search_query, None)
        if author:
            author_id = author.get("author_id", "")
            if author_id:
                return f"https://scholar.google.com/citations?user={author_id}&hl=en"
    except Exception as exc:
        logger.debug(f"Google Scholar search failed: {exc}")

    # Fallback: return search link
    return f"https://scholar.google.com/citations?view_op=search_authors&mauthors={quote_plus(author_name)}&hl=en"


# ============ Wikipedia ============

WIKI_HEADERS = {
    "User-Agent": "ScholarProfileBuilder/1.0 (academic research tool; contact: admin@example.com)"
}


def find_wikipedia_url(author_name: str) -> str:
    """Search for Wikipedia page."""
    api_url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": author_name,
        "srlimit": 5,
        "format": "json",
    }
    resp = request_get(api_url, params=params, timeout=10, proxies=_get_proxies(), headers=WIKI_HEADERS)
    if not resp:
        return ""

    try:
        results = resp.json().get("query", {}).get("search", [])
    except Exception:
        return ""

    last_name = author_name.split()[-1].lower()
    for r in results:
        title = r.get("title", "")
        if last_name in title.lower():
            return f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"

    return ""


# ============ Web Search (Tavily) ============

def tavily_search(query: str, api_key: str, max_results: int = 5) -> List[Tuple[str, str]]:
    """Search using Tavily Search API, return [(title, url), ...]"""
    if not api_key:
        return []

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        resp = client.search(query=query, max_results=max_results, search_depth="basic")
        results = []
        for item in resp.get("results", []):
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if title and url:
                results.append((title, url))
        return results
    except Exception as exc:
        logger.warning(f"Tavily search failed for '{query}': {exc}")
        return []


def search_scholar_resources(author_name: str, search_api_key: str) -> ScholarSources:
    """Search for various scholar resources using Tavily."""
    sources = ScholarSources(author_name=author_name)

    if not search_api_key:
        logger.info("No search API key provided, skipping web search")
        return sources

    queries = {
        "interview": f'"{author_name}" interview research',
        "lab": f'"{author_name}" lab homepage research group',
        "news": f'"{author_name}" professor news award',
    }

    seen_urls: Set[str] = set()

    for category, query in queries.items():
        results = tavily_search(query, search_api_key, max_results=5)
        for title, url in results:
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Filter low-quality sources
            domain = urlparse(url).hostname or ""
            skip_domains = {"youtube.com", "twitter.com", "facebook.com", "instagram.com",
                           "reddit.com", "linkedin.com", "scholar.google.com",
                           "dblp.org", "semanticscholar.org", "researchgate.net"}
            if any(d in domain for d in skip_domains):
                continue

            if category == "interview":
                sources.news_interview_urls.append((title, url))
            elif category == "lab":
                sources.lab_urls.append(url)
            elif category == "news":
                sources.news_interview_urls.append((title, url))

        time.sleep(0.5)  # Rate limiting

    return sources


# ============ Proxy utilities ============

_proxies_cache: Optional[Dict[str, str]] = None

def _get_proxies() -> Optional[Dict[str, str]]:
    """Get proxy configuration from environment variables."""
    global _proxies_cache
    if _proxies_cache is not None:
        return _proxies_cache

    proxies = {}
    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    _proxies_cache = proxies or None
    return _proxies_cache


# ============ URL validation and filtering ============

def _is_generic_homepage(url: str) -> bool:
    """Check if URL is a too-generic homepage (only domain root, no specific page)."""
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    if not path or path in ("/", "/index.html", "/index.php"):
        return True
    generic_patterns = ["/wiki/", "/search", "/category/", "/tag/"]
    if any(p in path.lower() for p in generic_patterns):
        return True
    return False


def _page_mentions_author(url: str, author_name: str, timeout: int = 15) -> bool:
    """Check if page content mentions the author (for filtering uncertain URLs)."""
    resp = request_get(url, timeout=timeout, proxies=_get_proxies(), headers=DEFAULT_HEADERS)
    if not resp:
        return False
    text = resp.text[:50000].lower()
    name_lower = author_name.lower()
    return name_lower in text


def validate_and_filter_urls(sources: ScholarSources, verify: bool = True) -> ScholarSources:
    """Validate and filter URLs, remove too-generic or irrelevant links."""
    last_name = sources.author_name.split()[-1].lower() if sources.author_name else ""

    # 1. Filter generic homepages
    filtered_homepages = []
    for url in sources.homepage_urls:
        if _is_generic_homepage(url):
            logger.debug(f"Filtered generic homepage: {url}")
            continue
        filtered_homepages.append(url)
    sources.homepage_urls = filtered_homepages

    # 2. Filter generic institution pages
    filtered_institutions = []
    for url in sources.institution_urls:
        if _is_generic_homepage(url):
            logger.debug(f"Filtered generic institution: {url}")
            continue
        filtered_institutions.append(url)
    sources.institution_urls = filtered_institutions

    # 3. Filter Google Scholar search links (not profile pages)
    if sources.google_scholar_url and "search_authors" in sources.google_scholar_url:
        logger.debug(f"Filtered Scholar search link: {sources.google_scholar_url}")
        sources.google_scholar_url = ""

    # 4. Verify lab URLs and interview URLs actually mention the author
    if verify and last_name:
        filtered_lab = []
        for url in sources.lab_urls:
            if _page_mentions_author(url, sources.author_name):
                filtered_lab.append(url)
                logger.debug(f"Verified lab URL: {url}")
            else:
                logger.debug(f"Filtered lab URL (author not mentioned): {url}")
        sources.lab_urls = filtered_lab

        filtered_interviews = []
        for title, url in sources.news_interview_urls:
            if last_name in title.lower():
                filtered_interviews.append((title, url))
                continue
            if _page_mentions_author(url, sources.author_name):
                filtered_interviews.append((title, url))
                logger.debug(f"Verified interview URL: {url}")
            else:
                logger.debug(f"Filtered interview URL (author not mentioned): {url}")
        sources.news_interview_urls = filtered_interviews

    return sources


# ============ Collection and output ============

def collect_all_sources(
    author_name: str,
    dblp_url: str,
    search_api_key: str = "",
) -> ScholarSources:
    """Collect all data sources."""
    sources = ScholarSources(author_name=author_name, dblp_url=dblp_url)

    logger.info(f"[1/5] Fetching DBLP info for {author_name}")
    dblp_name, dblp_homepages = fetch_dblp_info(dblp_url)
    if dblp_name:
        author_name = dblp_name
        sources.author_name = dblp_name
    sources.homepage_urls.extend(dblp_homepages)

    logger.info(f"[2/5] Fetching OpenAlex info")
    oa_homepage, oa_institutions = fetch_openalex_info(author_name)
    if oa_homepage and oa_homepage not in sources.homepage_urls:
        sources.homepage_urls.append(oa_homepage)
    sources.institution_urls.extend(oa_institutions)

    logger.info(f"[3/5] Searching Google Scholar")
    gs_url = find_google_scholar_url(author_name)
    if gs_url:
        sources.google_scholar_url = gs_url

    logger.info(f"[4/5] Searching Wikipedia")
    wiki_url = find_wikipedia_url(author_name)
    if wiki_url:
        sources.wikipedia_url = wiki_url

    logger.info(f"[5/5] Searching web for interviews and lab pages")
    web_sources = search_scholar_resources(author_name, search_api_key)
    sources.news_interview_urls.extend(web_sources.news_interview_urls)
    sources.lab_urls.extend(web_sources.lab_urls)

    # Dedup
    sources.homepage_urls = _dedup(sources.homepage_urls)
    sources.institution_urls = _dedup(sources.institution_urls)
    sources.lab_urls = _dedup(sources.lab_urls)

    # Validate and filter
    logger.info("Validating and filtering URLs...")
    sources = validate_and_filter_urls(sources, verify=True)

    return sources


def _dedup(urls: List[str]) -> List[str]:
    seen = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def format_sources_txt(sources: ScholarSources) -> str:
    """Format as standard sources.txt."""
    lines = [f"# {sources.author_name} - Auto-discovered resources", ""]

    lines.append("## DBLP")
    lines.append(sources.dblp_url)
    lines.append("")

    if sources.homepage_urls:
        lines.append("## Personal/Lab Homepages")
        for u in sources.homepage_urls:
            lines.append(u)
        lines.append("")

    if sources.institution_urls:
        lines.append("## Institution Pages")
        for u in sources.institution_urls:
            lines.append(u)
        lines.append("")

    if sources.lab_urls:
        lines.append("## Labs/Research Groups")
        for u in sources.lab_urls:
            lines.append(u)
        lines.append("")

    if sources.google_scholar_url:
        lines.append("## Google Scholar")
        lines.append(sources.google_scholar_url)
        lines.append("")

    if sources.wikipedia_url:
        lines.append("## Wikipedia")
        lines.append(sources.wikipedia_url)
        lines.append("")

    if sources.news_interview_urls:
        lines.append("## Interviews/News")
        for title, url in sources.news_interview_urls:
            lines.append(url)
        lines.append("")

    return "\n".join(lines)


# ============ Callable interface ============

def collect_and_save_sources(
    author_name: str,
    dblp_url: str,
    output_path: str,
    search_api_key: str = "",
    verify: bool = True,
) -> Tuple[bool, str]:
    """Collect scholar resources and save to file. Returns (success, message)."""
    try:
        sources = collect_all_sources(author_name, dblp_url, search_api_key)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        content = format_sources_txt(sources)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        url_count = content.count("http")
        return True, f"{url_count} URLs -> {output_path}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ============ Main function ============

def main():
    parser = argparse.ArgumentParser(
        description="Auto-discover scholar resource links, output standard sources.txt file."
    )
    parser.add_argument("--author", default="", help="Scholar name (single mode)")
    parser.add_argument("--dblp-url", default="", help="DBLP homepage URL (single mode)")
    parser.add_argument("--output", default="", help="Output file path (single mode)")
    parser.add_argument("--csv", default="", help="CSV file path (batch mode, needs author,dbl_url,sources_file columns)")
    parser.add_argument("--tavily-key", default="", help="Tavily Search API key (or set TAVILY_API_KEY env var)")
    parser.add_argument("--http-proxy", default="", help="HTTP proxy")
    parser.add_argument("--https-proxy", default="", help="HTTPS proxy")
    parser.add_argument("--skip-existing", default="true", help="Skip scholars with existing sources files (true/false)")

    args = parser.parse_args()

    # Set proxy
    if args.http_proxy:
        os.environ["HTTP_PROXY"] = args.http_proxy.strip()
    if args.https_proxy:
        os.environ["HTTPS_PROXY"] = args.https_proxy.strip()

    search_key = (args.tavily_key or os.environ.get("TAVILY_API_KEY") or "").strip()
    skip_existing = str(args.skip_existing).strip().lower() in {"1", "true", "yes", "y", "on"}

    # Batch mode
    if args.csv:
        _run_batch(args.csv, search_key, skip_existing)
        return

    # Single mode
    if not args.author or not args.dblp_url or not args.output:
        parser.error("Single mode requires --author, --dblp-url, --output; or use --csv for batch mode.")

    ok, msg = collect_and_save_sources(
        args.author.strip(), args.dblp_url.strip(), args.output.strip(), search_key
    )
    print(f"[{'OK' if ok else 'FAIL'}] {args.author} -> {msg}")


def _run_batch(csv_path: str, search_key: str, skip_existing: bool):
    """Batch mode: read scholar list from CSV, collect resources one by one."""
    import csv as csv_mod

    csv_file = Path(csv_path)
    if not csv_file.exists():
        print(f"CSV not found: {csv_file}")
        sys.exit(1)

    rows = []
    with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv_mod.DictReader(f)
        for item in reader:
            author = (item.get("author") or "").strip()
            dblp_url = (item.get("dblp_url") or "").strip()
            sources_file = (item.get("sources_file") or "").strip()
            if author and dblp_url and sources_file:
                rows.append((author, dblp_url, sources_file))

    if not rows:
        print(f"No valid rows in CSV: {csv_file}")
        sys.exit(1)

    print(f"[INFO] Found {len(rows)} scholars in CSV")
    ok_count = 0
    fail_count = 0

    for i, (author, dblp_url, sources_file) in enumerate(rows, 1):
        src_path = Path(sources_file)
        if not src_path.is_absolute():
            src_path = csv_file.parent / src_path
        src_path = src_path.resolve()

        if skip_existing and src_path.exists() and src_path.stat().st_size > 50:
            print(f"[{i}/{len(rows)}] SKIP {author} (sources file exists)")
            ok_count += 1
            continue

        print(f"[{i}/{len(rows)}] Processing {author}...")
        ok, msg = collect_and_save_sources(author, dblp_url, str(src_path), search_key)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {msg}")
        if ok:
            ok_count += 1
        else:
            fail_count += 1
        time.sleep(1)  # Rate limiting

    print(f"\n[SUMMARY] Total: {len(rows)}, OK: {ok_count}, Failed: {fail_count}")


# ============ Callable interface for pipeline ============

def run_single(scholar_name: str, output_dir: str, csv_path: str = None,
               seed_urls: list = None, search_api_key: str = "") -> str:
    """Discover sources for a single scholar and save to file.

    Args:
        scholar_name: Full name of the scholar.
        output_dir: Output directory path.
        csv_path: Path to CSV (to read dblp_url). If None, searches DBLP directly.
        seed_urls: Optional seed URLs to include.
        search_api_key: Tavily API key for web search.

    Returns:
        Path to the generated sources file.
    """
    import csv as csv_mod
    from utils import safe_name

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    safe = safe_name(scholar_name)
    sources_path = out / f"{safe}_sources.txt"

    # Get dblp_url from CSV or search DBLP
    dblp_url = ""
    if csv_path and Path(csv_path).exists():
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                if (row.get("author") or "").strip().lower() == scholar_name.lower():
                    dblp_url = (row.get("dblp_url") or "").strip()
                    break

    if not dblp_url:
        from build_scholars_csv import search_dblp_author
        _, dblp_url = search_dblp_author(scholar_name)
        if not dblp_url:
            raise RuntimeError(f"Could not find DBLP URL for '{scholar_name}'.")

    if not search_api_key:
        search_api_key = os.environ.get("TAVILY_API_KEY", "")

    ok, msg = collect_and_save_sources(
        scholar_name, dblp_url, str(sources_path), search_api_key
    )
    if not ok:
        raise RuntimeError(f"Source discovery failed for '{scholar_name}': {msg}")

    # Append seed URLs if provided
    if seed_urls:
        with open(sources_path, "a", encoding="utf-8") as f:
            f.write("\n## Seed URLs\n")
            for url in seed_urls:
                if url and url.strip():
                    f.write(url.strip() + "\n")

    print(f"[build_scholar_sources] {msg}")
    return str(sources_path)


if __name__ == "__main__":
    main()
