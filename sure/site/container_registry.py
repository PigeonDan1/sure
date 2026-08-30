#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import ProxyHandler, Request, build_opener

SEMVER_TAG_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
REGISTRY_TAG_LIST_LIMIT = 1000
REGISTRY_TAG_TIMEOUT_SECONDS = 30


def _docker_registry_credentials(registry: str) -> tuple[str, str] | None:
    """Read Basic credentials from Docker's config without exposing them."""
    configured = os.environ.get("DOCKER_CONFIG", "").strip()
    config_path = (
        Path(configured).expanduser() / "config.json"
        if configured
        else Path.home() / ".docker" / "config.json"
    )
    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("auths"), dict):
        return None
    auths = document["auths"]
    normalized = registry.removeprefix("http://").removeprefix("https://").rstrip("/")
    candidates = (registry, normalized, f"http://{normalized}", f"https://{normalized}")
    for key in candidates:
        entry = auths.get(key)
        if not isinstance(entry, dict):
            continue
        encoded = entry.get("auth")
        if isinstance(encoded, str) and encoded:
            try:
                decoded = base64.b64decode(encoded).decode("utf-8")
            except (ValueError, UnicodeError):
                continue
            username, separator, password = decoded.partition(":")
            if separator:
                return username, password
        identity_token = entry.get("identitytoken")
        if isinstance(identity_token, str) and identity_token:
            return "", identity_token
    return None


def _registry_base_url(repository: str) -> tuple[str, str, str]:
    raw = repository
    if "://" not in raw:
        raw = f"{os.environ.get('SURE_REGISTRY_SCHEME', 'http')}://{raw}"
    parsed = urlparse(raw)
    if not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"invalid registry repository: {repository!r}")
    repo_path = parsed.path.strip("/")
    endpoint = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            f"/v2/{repo_path}/tags/list",
            "",
            urlencode({"n": REGISTRY_TAG_LIST_LIMIT}),
            "",
        )
    )
    return endpoint, parsed.netloc, repo_path


def _basic_authorization(credentials: tuple[str, str] | None) -> str | None:
    if credentials is None:
        return None
    username, password = credentials
    if username == "" and password:
        return f"Bearer {password}"
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {encoded}"


def _bearer_challenge(header: str) -> dict[str, str]:
    scheme, _, parameters = header.partition(" ")
    if scheme.lower() != "bearer":
        return {}
    return {
        key: value
        for key, value in re.findall(r'([A-Za-z][A-Za-z0-9_-]*)="([^"]*)"', parameters)
    }


def _registry_json_request(url: str, headers: dict[str, str]) -> tuple[dict, dict[str, str]]:
    request = Request(url, headers=headers)
    try:
        with build_opener(ProxyHandler({})).open(
            request, timeout=REGISTRY_TAG_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("registry response is not a JSON object")
            return payload, {str(key): str(value) for key, value in response.headers.items()}
    except HTTPError:
        raise
    except (OSError, URLError, ValueError, UnicodeError) as error:
        raise ValueError(f"registry tag query failed: {error}") from error


def _next_registry_page(headers: dict[str, str], current_url: str) -> str | None:
    link = next((value for key, value in headers.items() if key.lower() == "link"), "")
    for entry in link.split(","):
        match = re.match(r'\s*<([^>]+)>\s*;\s*rel="?next"?', entry)
        if match:
            return urljoin(current_url, match.group(1))
    return None


def _collect_registry_tags(
    endpoint: str,
    headers: dict[str, str],
    first_page: tuple[dict, dict[str, str]] | None = None,
) -> list[str]:
    tags: set[str] = set()
    page_url = endpoint
    page = first_page
    while page_url:
        payload, response_headers = (
            page if page is not None else _registry_json_request(page_url, headers)
        )
        page = None
        page_tags = payload.get("tags")
        if page_tags is not None:
            if not isinstance(page_tags, list) or not all(
                isinstance(tag, str) for tag in page_tags
            ):
                raise ValueError("registry tag query returned an invalid tags list")
            tags.update(page_tags)
        page_url = _next_registry_page(response_headers, page_url)
    return sorted(tags)


def registry_tags(repository: str) -> list[str]:
    """List tags for one repository through the Docker Registry V2 API."""
    endpoint, registry, _ = _registry_base_url(repository)
    authorization = _basic_authorization(_docker_registry_credentials(registry))
    headers = {"Accept": "application/json"}
    if authorization:
        headers["Authorization"] = authorization
    try:
        first_page = _registry_json_request(endpoint, headers)
    except HTTPError as error:
        if error.code == 404:
            return []
        if error.code != 401:
            raise ValueError(
                f"registry tag query returned HTTP {error.code} for {repository}"
            ) from error
        challenge = _bearer_challenge(str(error.headers.get("WWW-Authenticate") or ""))
        realm = challenge.get("realm")
        if not realm:
            raise ValueError(f"registry tag query requires authentication for {repository}") from error
        parsed_realm = urlparse(realm)
        query = dict(parse_qsl(parsed_realm.query, keep_blank_values=True))
        for key in ("service", "scope"):
            if challenge.get(key):
                query[key] = challenge[key]
        token_url = urlunparse(parsed_realm._replace(query=urlencode(query)))
        token_headers = {"Accept": "application/json"}
        if authorization and authorization.startswith("Basic "):
            token_headers["Authorization"] = authorization
        try:
            token_payload, _ = _registry_json_request(token_url, token_headers)
        except HTTPError as token_error:
            raise ValueError(
                f"registry authentication failed for {repository} (HTTP {token_error.code})"
            ) from token_error
        token = token_payload.get("token") or token_payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError(f"registry authentication returned no bearer token for {repository}")
        authenticated_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        try:
            return _collect_registry_tags(endpoint, authenticated_headers)
        except HTTPError as authenticated_error:
            raise ValueError(
                f"registry tag query remained unauthorized for {repository} "
                f"(HTTP {authenticated_error.code})"
            ) from authenticated_error
    return _collect_registry_tags(endpoint, headers, first_page)


def next_image_version(tags: Iterable[str]) -> str:
    """Choose the next unused patch version after the highest SemVer tag."""
    tag_set = {str(tag) for tag in tags}
    parsed = []
    for tag in tag_set:
        match = SEMVER_TAG_RE.fullmatch(tag)
        if match:
            parsed.append(tuple(int(part) for part in match.groups()))
    major, minor, patch = max(parsed, default=(0, 1, -1))
    candidate = (major, minor, patch + 1)
    while ".".join(str(part) for part in candidate) in tag_set:
        candidate = (candidate[0], candidate[1], candidate[2] + 1)
    return ".".join(str(part) for part in candidate)


def resolve_image_version(
    repositories: Iterable[str],
    requested: str | None = None,
    *,
    tag_reader: Callable[[str], list[str]] | None = None,
) -> tuple[str, dict[str, object]]:
    """Resolve an explicit version or the next free version across repositories."""
    repository_list = list(dict.fromkeys(repositories))
    if requested is not None:
        return requested, {"mode": "explicit", "repositories": [], "existing_tags": []}
    if not repository_list:
        raise ValueError("at least one container repository is required for automatic versioning")
    reader = tag_reader or registry_tags
    tags_by_repository = {
        repository: reader(repository) for repository in repository_list
    }
    all_tags = sorted({tag for tags in tags_by_repository.values() for tag in tags})
    return next_image_version(all_tags), {
        "mode": "registry_auto",
        "repositories": repository_list,
        "existing_tags": all_tags,
        "tags_by_repository": tags_by_repository,
    }
