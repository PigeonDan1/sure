#!/usr/bin/env python3
"""
Common utilities module.
Shared functions for file traversal, URL processing, text normalization, etc.
"""

import logging
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

import requests

# ============ Logging ============

def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    """Configure a logger with stream handler."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


# ============ Text processing ============

def normalize_text(text: str) -> str:
    """Normalize text: Unicode NFKC, collapse whitespace, lowercase."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def canonical_title_key(title: str) -> str:
    """Canonicalize title for deduplication, ignoring case and punctuation."""
    t = normalize_text(title)
    t = re.sub(r"[\W_]+", "", t, flags=re.UNICODE)
    return t


def cut_text(text: str, max_len: int) -> str:
    """Truncate text to specified length."""
    text = normalize_text(text)
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "..."


def sanitize_filename(name: str) -> str:
    """Remove illegal characters from filename."""
    name = normalize_text(name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name or "unknown"


def is_valid_xml_char(ch: str) -> bool:
    """Check if character is a valid XML character."""
    code = ord(ch)
    return (
        code == 0x9
        or code == 0xA
        or code == 0xD
        or 0x20 <= code <= 0xD7FF
        or 0xE000 <= code <= 0xFFFD
        or 0x10000 <= code <= 0x10FFFF
    )


def xml_safe_text(text: str) -> str:
    """Remove invalid XML characters."""
    if text is None:
        return ""
    s = str(text)
    return "".join(ch for ch in s if is_valid_xml_char(ch))


# ============ URL processing ============

def extract_urls_from_content(content: str) -> List[str]:
    """Extract all URLs from text content."""
    url_pattern = r'https?://[^\s\)\]\"\']+'
    urls = re.findall(url_pattern, content)
    # Clean trailing punctuation
    cleaned = [url.rstrip('.,;:!?') for url in urls]
    return list(set(cleaned))


def classify_url(url: str, context_line: str = '') -> str:
    """Classify a URL by type."""
    url_lower = url.lower()
    context_lower = context_line.lower()

    # Academic databases
    if 'dblp.org' in url_lower:
        return 'dblp'
    if 'scholar.google' in url_lower:
        return 'google_scholar'
    if 'orcid.org' in url_lower:
        return 'orcid'

    # Personal homepages
    homepage_keywords = [
        'personal', 'homepage', 'faculty', 'people', 'profile',
        'edu/', 'ac.uk/', 'ac.jp/', '.edu.', 'university',
        'lab', 'group', 'cv', 'curriculum', 'homepage'
    ]
    if any(kw in url_lower or kw in context_lower for kw in homepage_keywords):
        return 'homepage'

    # Academic platforms
    academic_platforms = [
        'researchgate', 'semanticscholar', 'arxiv', 'ieee',
        'acm.org', 'springer', 'nature', 'science',
        'sciencedirect', 'wiley', 'elsevier'
    ]
    if any(platform in url_lower for platform in academic_platforms):
        return 'academic_platform'

    # Media coverage
    media_keywords = ['news', 'interview', 'article', 'blog', 'media', 'press']
    if any(kw in url_lower or kw in context_lower for kw in media_keywords):
        return 'media'

    # Video
    if 'youtube' in url_lower or 'youtu.be' in url_lower:
        return 'video'

    return 'other'


def normalize_url(url: str) -> str:
    """Normalize URL (remove trailing slash, etc.)."""
    if not url:
        return ""
    url = url.strip()
    url = url.rstrip('/')
    return url


def extract_dblp_pid(dblp_url: str) -> Optional[str]:
    """Extract PID from DBLP URL."""
    if not dblp_url:
        return None

    url = dblp_url.strip()

    # Direct extraction from URL
    m = re.search(r"/pid/([^?#]+?)(?:\.html|\.xml)?(?:[?#].*)?$", url)
    if m:
        return m.group(1).strip("/")

    return None


def normalize_doi(doi_value: str) -> str:
    """Normalize DOI format."""
    if not doi_value:
        return ""
    doi_value = doi_value.strip()
    doi_value = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi_value, flags=re.IGNORECASE)
    return doi_value.strip().strip("/")


def extract_arxiv_id(arxiv_value: str) -> str:
    """Extract standard ID from arXiv URL or ID string."""
    if not arxiv_value:
        return ""
    v = arxiv_value.strip()
    v = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", v, flags=re.IGNORECASE)
    v = re.sub(r"\.pdf$", "", v, flags=re.IGNORECASE)
    return v.strip("/")


def extract_scholar_user_id(profile_url: str) -> Optional[str]:
    """Extract user ID from Google Scholar URL."""
    if not profile_url:
        return None
    try:
        parsed = urlparse(profile_url.strip())
        query = parse_qs(parsed.query)
        user_ids = query.get("user", [])
        if user_ids and user_ids[0].strip():
            return user_ids[0].strip()
    except Exception:
        return None
    return None


# ============ File handling ============

def get_project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).parent.resolve()


def find_scholar_files(directory: Path, pattern: str = "*_sources.txt") -> List[Path]:
    """Find scholar data files in directory."""
    files = sorted(directory.glob(pattern))
    return files


def read_file_safe(file_path: Path, encoding: str = 'utf-8') -> Optional[str]:
    """Safely read file content."""
    try:
        return file_path.read_text(encoding=encoding)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to read file {file_path}: {e}")
        return None


def write_file_safe(file_path: Path, content: str, encoding: str = 'utf-8') -> bool:
    """Safely write file content."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding=encoding)
        return True
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to write file {file_path}: {e}")
        return False


# ============ Name processing ============

def safe_name(author: str) -> str:
    """Convert author name to a safe filename."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", (author or "").strip())
    name = name.strip("_")
    return name or "unknown_author"


def split_authors(authors_raw: str) -> List[str]:
    """Split author list string."""
    if not authors_raw:
        return []
    s = authors_raw.replace(" and ", ",")
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return parts


def get_author_position(author_name: str, authors_raw: str) -> int:
    """Get author position in author list (1-based)."""
    authors = split_authors(authors_raw)
    if not authors:
        return 0
    target = normalize_text(author_name)
    for idx, name in enumerate(authors, 1):
        normalized = normalize_text(name)
        if target in normalized or normalized in target:
            return idx
    return 0


def is_top_n_author(author_name: str, authors_raw: str, n: int = 3) -> bool:
    """Check if author is in top N authors."""
    authors = split_authors(authors_raw)
    if not authors:
        return False
    target = normalize_text(author_name)
    for name in authors[:n]:
        normalized = normalize_text(name)
        if target in normalized or normalized in target:
            return True
    return False


# ============ Proxy configuration ============

def configure_proxies(http_proxy: str = "", https_proxy: str = "") -> Optional[Dict[str, str]]:
    """Configure HTTP proxies."""
    http_proxy = (http_proxy or "").strip()
    https_proxy = (https_proxy or "").strip()

    proxies = {}
    if http_proxy:
        proxies["http"] = http_proxy
        os.environ["HTTP_PROXY"] = http_proxy
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("http_proxy", None)

    if https_proxy:
        proxies["https"] = https_proxy
        os.environ["HTTPS_PROXY"] = https_proxy
    else:
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("https_proxy", None)

    return proxies or None


def get_proxies_from_env() -> Dict[str, str]:
    """Get proxy configuration from environment variables."""
    proxies = {}
    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or ""
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or ""

    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy

    return proxies


# ============ URL counting ============

def count_links(content: str) -> int:
    """Count the number of links in text."""
    lines = content.split('\n')
    count = 0
    for line in lines:
        if 'http://' in line or 'https://' in line:
            count += 1
    return count


# ============ Constants ============

# Default request headers
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# Default timeout
DEFAULT_TIMEOUT = 30

# Long timeout for known slow sites
LONG_TIMEOUT = 60

SLOW_HOSTS = {"arxiv.org", "export.arxiv.org", "www.wikidata.org", "www.osti.gov",
              "www.ssrn.com", "www.researchgate.net"}


def get_timeout_for_url(url: str) -> int:
    """Determine timeout for a given URL."""
    try:
        host = urlparse(url).hostname or ""
        return LONG_TIMEOUT if any(h in host for h in SLOW_HOSTS) else DEFAULT_TIMEOUT
    except Exception:
        return DEFAULT_TIMEOUT


def request_get_with_retry(
    url: str,
    max_retries: int = 3,
    backoff: float = 2.0,
    timeout: Optional[int] = None,
    **kwargs,
) -> Optional[requests.Response]:
    """GET request with retry and exponential backoff."""
    _timeout = timeout or get_timeout_for_url(url)
    kwargs.setdefault("timeout", _timeout)
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(backoff * (2 ** attempt))
    return None

# Academic style dimensions
STYLE_DIMENSIONS = [
    ("Problem-driven", "Method-driven"),
    ("Theory-oriented", "Application-oriented"),
    ("System-integration", "Modular-breakthrough"),
    ("Conservative-iteration", "Radical-exploration"),
    ("Empirical-induction", "Mechanistic-explanation"),
    ("Engineering-evidence", "Formal-modeling"),
]
