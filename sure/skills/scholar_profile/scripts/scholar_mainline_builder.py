#!/usr/bin/env python3
"""
Build main research-line graph from scholar homepage/interview sources.
Uses LLM to extract research themes and their evolution over time.
"""

import argparse
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


REQUEST_TIMEOUT = 20
LLM_REQUEST_TIMEOUT = 600
DEFAULT_BASE_URL = "http://58.210.177.113:8888/v1"
DEFAULT_MODEL = "mimo-v2-flash"
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


@dataclass
class SourceDoc:
    source: str
    text: str


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def request_get(url: str, proxies: Optional[dict] = None):
    return requests.get(url, headers=HTTP_HEADERS, timeout=REQUEST_TIMEOUT, proxies=proxies)


_URL_RE = re.compile(r'https?://[^\s\)\]\>\"\'“”]+')


def load_urls(args_urls: List[str], source_file: str) -> List[str]:
    urls = [u.strip() for u in (args_urls or []) if (u or "").strip()]
    if source_file:
        try:
            with open(source_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = _URL_RE.search(line)
                    if m:
                        urls.append(m.group(0).rstrip(".,;:"))
        except Exception:
            pass
    dedup = []
    seen = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


SOCIAL_MEDIA_DOMAINS = {
    "twitter.com", "x.com", "facebook.com", "instagram.com",
    "reddit.com", "tiktok.com", "linkedin.com", "youtube.com",
    "weibo.com", "zhihu.com",
}

# Trusted academic/institutional domains — always scrape these
TRUSTED_ACADEMIC_DOMAINS = {
    "scholar.google.com", "openreview.net", "dblp.org", "arxiv.org",
    "semanticscholar.org", "acm.org", "ieee.org", "springer.com",
    "nature.com", "sciencedirect.com", "dl.acm.org", "ams.org",
    "mathgenealogy.org", "orcid.org", "wikidata.org", "loc.gov",
    "d-nb.info", "amturing.acm.org", "awards.acm.org",
}


def extract_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # Remove irrelevant tags
    for t in soup(["script", "style", "noscript", "nav", "footer", "header",
                   "aside", "form", "iframe", "svg"]):
        t.decompose()
    # Prefer article / main content
    main = soup.find("article") or soup.find("main") or soup.find("div", {"role": "main"})
    target = main if main else soup
    blocks = []
    for tag in target.find_all(["h1", "h2", "h3", "p", "li", "blockquote"]):
        line = " ".join(tag.get_text(" ", strip=True).split())
        if len(line) >= 20:
            blocks.append(line)
    text = "\n".join(blocks).strip()
    if not text:
        text = " ".join(soup.get_text(" ", strip=True).split())
    return text


def clean_source_text(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cleaned = []
    noisy_line_pattern = re.compile(
        r"(doi|arxiv|proc\.|proceedings|journal|volume|vol\.|pp\.|pages?|isbn|issn|acl|emnlp|naacl|icml|neurips|aaai|ijcai)",
        re.IGNORECASE,
    )
    # Common web noise keywords
    noise_keywords = re.compile(
        r"(cookie|privacy|subscribe|newsletter|sign up|log in|register|advertisement|"
        r"click here|read more|share this|follow us|terms of use|all rights reserved)",
        re.IGNORECASE,
    )
    for ln in lines:
        if len(ln) < 30:
            continue
        if noisy_line_pattern.search(ln):
            continue
        if noise_keywords.search(ln):
            continue
        years = re.findall(r"\b(19|20)\d{2}\b", ln)
        digits = re.findall(r"\d", ln)
        if len(years) >= 2 or len(digits) >= max(12, len(ln) // 4):
            continue
        cleaned.append(ln)
    return "\n".join(cleaned)


def fetch_source_docs(urls: List[str], proxies: Optional[dict] = None) -> List[SourceDoc]:
    docs: List[SourceDoc] = []
    non_trusted_count = 0
    MAX_NON_TRUSTED = 3  # Max 3 non-academic pages

    for url in urls:
        # Skip social media
        try:
            host = urlparse(url).hostname or ""
            if any(d in host for d in SOCIAL_MEDIA_DOMAINS):
                continue
        except Exception:
            continue

        is_trusted = any(d in host for d in TRUSTED_ACADEMIC_DOMAINS)

        # Limit non-academic sources
        if not is_trusted and non_trusted_count >= MAX_NON_TRUSTED:
            continue

        try:
            resp = request_get(url, proxies=proxies)
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").lower()
            if "html" not in ctype and "<html" not in resp.text[:2000].lower():
                continue
            text = extract_text_from_html(resp.text)
            if len(text) < 80:
                continue
            # Non-academic domains: truncate to 1500 chars
            if not is_trusted:
                text = text[:1500]
                non_trusted_count += 1
            docs.append(SourceDoc(source=url, text=text))
        except Exception:
            continue
    return docs


def extract_dblp_pid(dblp_url: str) -> Optional[str]:
    if not dblp_url:
        return None
    m = re.search(r"/pid/([^?#]+?)(?:\.html|\.xml)?(?:[?#].*)?$", dblp_url.strip())
    if m:
        return m.group(1).strip("/")
    return None


def fetch_dblp_titles_by_year(dblp_url: str, proxies: Optional[dict] = None) -> Dict[int, List[str]]:
    pid = extract_dblp_pid(dblp_url)
    if not pid:
        return {}
    xml_url = f"https://dblp.org/pid/{pid}.xml"
    try:
        resp = request_get(xml_url, proxies=proxies)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception:
        return {}

    year_titles: Dict[int, List[str]] = defaultdict(list)
    for r in root.findall("./r"):
        if not list(r):
            continue
        entry = list(r)[0]
        title_elem = entry.find("title")
        year_elem = entry.find("year")
        title = " ".join("".join(title_elem.itertext()).split()) if title_elem is not None else ""
        year_text = " ".join("".join(year_elem.itertext()).split()) if year_elem is not None else ""
        if not title or not year_text.isdigit():
            continue
        year_titles[int(year_text)].append(title)
    return dict(year_titles)


def crop_text(text: str, max_len: int) -> str:
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    return s[:max_len].rstrip() + "..."


def extract_json_object(text: str) -> dict:
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError("No JSON object found in LLM output.")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise RuntimeError("Extracted JSON is not a dict object.")
    return obj


def build_llm_prompt(
    author_name: str,
    docs: List[SourceDoc],
    seed_topics: List[str],
    year_titles: Dict[int, List[str]],
    max_chars_per_source: int,
) -> str:
    source_blocks = []
    for i, d in enumerate(docs, 1):
        source_blocks.append(
            "\n".join(
                [
                    f"[Source-{i}] URL: {d.source}",
                    f"[Source-{i}] Text: {crop_text(d.text, max_chars_per_source)}",
                ]
            )
        )
    source_text = "\n\n".join(source_blocks) if source_blocks else "No source text"

    year_lines = []
    for y in sorted(year_titles.keys()):
        titles = year_titles[y][:20]
        if titles:
            year_lines.append(f"{y}: " + " | ".join(titles))
    timeline_hint = "\n".join(year_lines) if year_lines else "No DBLP timeline hint"
    seed_line = ", ".join(seed_topics) if seed_topics else "None"

    return f"""You are a research-profile analysis assistant. Build a main research-line graph for scholar {author_name}.

Strict requirements:
1) Output JSON only.
2) Top-level fields must be:
   - scholar: string
   - nodes: array
   - edges: array
   - timeline: array
   - sources: array
3) Each node must contain:
   - id: \"n1\"/\"n2\"...
   - topic: 2-6 words, avoid person/org/venue names
   - weight: 0-1
   - first_year: int|null
   - last_year: int|null
   - evidence_count: int
   - aliases: string[]
4) Each edge must contain:
   - source: node id
   - target: node id
   - weight: 0-1
   - type: fixed \"co-evolution\"
5) Each timeline item must contain:
   - year: int
   - top_topics: string[] (1-3)
6) Focus on long-term research lines, not short-term noise.
7) If evidence is noisy, abstract to stable research directions.

Optional seed topics:
{seed_line}

DBLP timeline hint:
{timeline_hint}

Source texts:
{source_text}
"""


def call_llm_for_graph(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    proxies: Optional[dict] = None,
) -> dict:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a careful information extraction assistant."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 16000,
        "thinking": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    session = requests.Session()
    session.trust_env = False
    try:
        resp = session.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=LLM_REQUEST_TIMEOUT,
            proxies=proxies,
        )
    except requests.exceptions.ReadTimeout:
        if proxies:
            resp = session.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=LLM_REQUEST_TIMEOUT,
                proxies={},
            )
        else:
            raise

    resp.raise_for_status()
    data = resp.json()
    choice0 = (data.get("choices") or [{}])[0]
    message = choice0.get("message") or {}
    content = message.get("content")
    reasoning_content = message.get("reasoning_content")

    candidates: List[str] = []

    if isinstance(content, str) and content.strip():
        candidates.append(content.strip())
    if isinstance(reasoning_content, str) and reasoning_content.strip():
        candidates.append(reasoning_content.strip())

    if isinstance(content, list):
        chunks = []
        for c in content:
            if isinstance(c, dict):
                t = (
                    c.get("text")
                    or c.get("content")
                    or c.get("output_text")
                    or c.get("value")
                )
                if isinstance(t, str) and t.strip():
                    chunks.append(t.strip())
            elif isinstance(c, str) and c.strip():
                chunks.append(c.strip())
        if chunks:
            candidates.append("\n".join(chunks))

    for k in ("output_text", "text", "content"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())

    for text in candidates:
        try:
            return extract_json_object(text)
        except Exception:
            continue

    try:
        with open("llm_graph_raw_response.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    raise RuntimeError("No usable text found in LLM output. Raw response saved to llm_graph_raw_response.json.")


def build_sources_meta(docs: List[SourceDoc]) -> List[dict]:
    meta = []
    for d in docs:
        host = urlparse(d.source).netloc or d.source
        meta.append({"source": d.source, "host": host, "text_length": len(d.text)})
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Build main research-line graph from homepage/interview sources.")
    parser.add_argument("--author", required=True, help="Scholar name")
    parser.add_argument("--source-url", action="append", default=[], help="A-layer source URL (repeatable)")
    parser.add_argument("--source-file", default="", help="Text file of source URLs, one per line")
    parser.add_argument("--seed-topic", action="append", default=[], help="Optional seed topic (repeatable)")
    parser.add_argument("--dblp-url", default="", help="Optional DBLP URL for timeline extraction")
    parser.add_argument("--output", default="mainline_graph.json", help="Output JSON path")
    parser.add_argument("--http-proxy", default="", help="HTTP proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--https-proxy", default="", help="HTTPS proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"LLM API base URL, default {DEFAULT_BASE_URL}")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model name, default {DEFAULT_MODEL}")
    parser.add_argument("--api-key", default="", help="LLM API key; fallback to OPENAI_API_KEY")
    parser.add_argument(
        "--max-chars-per-source",
        type=int,
        default=7000,
        help="Maximum chars per source sent to LLM, default 7000",
    )
    args = parser.parse_args()

    proxies = {}
    if args.http_proxy.strip():
        proxies["http"] = args.http_proxy.strip()
    if args.https_proxy.strip():
        proxies["https"] = args.https_proxy.strip()
    proxies = proxies or None

    api_key = (args.api_key or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing API key. Please provide --api-key or OPENAI_API_KEY.")

    urls = load_urls(args.source_url, args.source_file)
    docs = fetch_source_docs(urls, proxies=proxies)
    cleaned_docs = [SourceDoc(source=d.source, text=clean_source_text(d.text)) for d in docs]
    year_titles = fetch_dblp_titles_by_year(args.dblp_url.strip(), proxies=proxies) if args.dblp_url.strip() else {}

    prompt = build_llm_prompt(
        author_name=args.author.strip(),
        docs=cleaned_docs,
        seed_topics=args.seed_topic,
        year_titles=year_titles,
        max_chars_per_source=max(1200, int(args.max_chars_per_source)),
    )
    graph = call_llm_for_graph(
        base_url=args.base_url,
        api_key=api_key,
        model=args.model,
        prompt=prompt,
        proxies=proxies,
    )

    if isinstance(graph, dict):
        graph["sources"] = build_sources_meta(cleaned_docs)
        graph["scholar"] = graph.get("scholar") or args.author.strip()

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    print(f"Done: {args.output}")
    print(f"Sources used: {len(docs)}")
    print(f"Topics: {len(graph.get('nodes') or [])}")


# ============ Callable interface for pipeline ============

def run_single(scholar_name: str, output_dir: str, source_file: str = None,
               dblp_url: str = "", api_key: str = "", base_url: str = DEFAULT_BASE_URL,
               model: str = DEFAULT_MODEL) -> str:
    """Build mainline graph for a single scholar.

    Args:
        scholar_name: Full name of the scholar.
        output_dir: Output directory path.
        source_file: Path to sources.txt file.
        dblp_url: Optional DBLP URL for timeline extraction.
        api_key: LLM API key.
        base_url: LLM API base URL.
        model: LLM model name.

    Returns:
        Path to the generated mainline JSON file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    output_path = out / "mainline_graph.json"

    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing API key for mainline extraction.")

    urls = load_urls([], source_file or "")
    docs = fetch_source_docs(urls, proxies=None)
    cleaned_docs = [SourceDoc(source=d.source, text=clean_source_text(d.text)) for d in docs]
    year_titles = fetch_dblp_titles_by_year(dblp_url, proxies=None) if dblp_url else {}

    prompt = build_llm_prompt(
        author_name=scholar_name,
        docs=cleaned_docs,
        seed_topics=[],
        year_titles=year_titles,
        max_chars_per_source=7000,
    )
    graph = call_llm_for_graph(
        base_url=base_url,
        api_key=api_key,
        model=model,
        prompt=prompt,
        proxies=None,
    )

    if isinstance(graph, dict):
        graph["sources"] = build_sources_meta(cleaned_docs)
        graph["scholar"] = graph.get("scholar") or scholar_name

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    print(f"[scholar_mainline_builder] Done: {output_path}, Topics: {len(graph.get('nodes') or [])}")
    return str(output_path)


if __name__ == "__main__":
    main()
