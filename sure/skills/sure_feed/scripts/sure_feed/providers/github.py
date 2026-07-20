from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any

from sure_feed.providers.base import ProviderRequest, append_query, http_get_json, http_get_text


GITHUB_API = "https://api.github.com"


class GitHubProvider:
    source = "github"

    def __init__(self, token: str | None = None, timeout: int = 20) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.timeout = timeout

    def _headers(self, raw: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
            "User-Agent": "sure-feed-online-discover",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def search(self, request: ProviderRequest) -> list[dict[str, Any]]:
        query = request.query or request.task
        if request.task.lower() not in query.lower():
            query = f"{query} {request.task}"
        payload = http_get_json(
            append_query(
                GITHUB_API,
                "/search/repositories",
                {"q": f"{query} in:name,description,readme", "per_page": request.max_models, "sort": "stars"},
            ),
            headers=self._headers(),
            timeout=self.timeout,
        )
        items = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            return []
        candidates: list[dict[str, Any]] = []
        for raw in items[: request.max_models]:
            if not isinstance(raw, dict):
                continue
            full_name = str(raw.get("full_name") or "")
            html_url = str(raw.get("html_url") or "")
            if not full_name or not html_url:
                continue
            details = self._repo_details(full_name)
            readme = self._readme(full_name)
            release = self._latest_release(full_name)
            weights_source, weak_reason = self._infer_weights_source(readme, release)
            tags = [str(item) for item in (details.get("topics") or raw.get("topics") or [])]
            description = str(raw.get("description") or details.get("description") or "")
            if readme:
                description = f"{description}\n{readme[:4000]}".strip()
            candidates.append(
                self._candidate_from_repo(raw, details, readme, release, weights_source, weak_reason)
            )
        return candidates

    def direct(self, full_name: str) -> dict[str, Any]:
        raw = self._repo_details(full_name)
        if not raw:
            raise ValueError(f"cannot fetch GitHub repo metadata: {full_name}")
        readme = self._readme(full_name)
        release = self._latest_release(full_name)
        weights_source, weak_reason = self._infer_weights_source(readme, release)
        return self._candidate_from_repo(raw, raw, readme, release, weights_source, weak_reason)

    def _candidate_from_repo(
        self,
        raw: dict[str, Any],
        details: dict[str, Any],
        readme: str,
        release: dict[str, Any] | None,
        weights_source: str,
        weak_reason: str,
    ) -> dict[str, Any]:
        full_name = str(raw.get("full_name") or details.get("full_name") or "")
        html_url = str(raw.get("html_url") or details.get("html_url") or f"https://github.com/{full_name}")
        tags = [str(item) for item in (details.get("topics") or raw.get("topics") or [])]
        description = str(raw.get("description") or details.get("description") or "")
        if readme:
            description = f"{description}\n{readme[:4000]}".strip()
        default_branch = str(details.get("default_branch") or raw.get("default_branch") or "main")
        supplemental_docs = self._supplemental_docs(full_name, default_branch, readme)
        model_card_text = "\n\n".join(part for part in [readme, *supplemental_docs] if part)
        return {
            "source": self.source,
            "model_id": full_name,
            "repo": html_url,
            "source_url": html_url,
            "model_card_url": f"{html_url}/blob/{default_branch}/README.md",
            "model_card_text": model_card_text,
            "readme": model_card_text,
            "tasks": [],
            "pipeline_tag": None,
            "tags": tags,
            "description": description,
            "license": (raw.get("license") or details.get("license") or {}).get("spdx_id")
            if isinstance(raw.get("license") or details.get("license"), dict)
            else None,
            "download_count": raw.get("watchers_count") or raw.get("stargazers_count"),
            "stars": raw.get("stargazers_count"),
            "forks": raw.get("forks_count"),
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
            "github_weights_source": weights_source,
            "github_weights_source_reason": weak_reason,
            "latest_release": release,
            "raw": raw,
        }

    def _supplemental_docs(self, full_name: str, default_branch: str, readme: str) -> list[str]:
        paths = self._repo_tree_paths(full_name, default_branch)
        selected: list[str] = []
        readme_lower = readme.lower()
        for path in paths:
            lower = path.lower()
            basename = lower.rsplit("/", 1)[-1]
            referenced = basename in readme_lower or path.lower() in readme_lower
            interesting = (
                ("/readme" in lower and lower not in {"readme.md", "readme"}) or
                basename in {"dockerfile", "requirements.txt", "pyproject.toml", "environment.yml"} or
                (any(token in lower for token in ("decode", "infer", "inference")) and lower.endswith((".sh", ".py", ".md"))) or
                (referenced and lower.endswith((".sh", ".py", ".md", ".txt", ".yml", ".yaml")))
            )
            if interesting:
                selected.append(path)
            if len(selected) >= 8:
                break
        docs: list[str] = []
        for path in selected:
            text = self._raw_file(full_name, default_branch, path)
            if text.strip():
                docs.append(f"## Retrieved GitHub file: {path}\n\n```{self._lang_for_path(path)}\n{text[:20_000]}\n```")
        return docs

    def _repo_tree_paths(self, full_name: str, default_branch: str) -> list[str]:
        try:
            payload = http_get_json(
                f"{GITHUB_API}/repos/{full_name}/git/trees/{urllib.parse.quote(default_branch, safe='')}?recursive=1",
                headers=self._headers(),
                timeout=self.timeout,
            )
        except Exception:
            return []
        tree = payload.get("tree") if isinstance(payload, dict) else []
        if not isinstance(tree, list):
            return []
        return [str(item.get("path")) for item in tree if isinstance(item, dict) and item.get("type") == "blob" and item.get("path")]

    def _raw_file(self, full_name: str, default_branch: str, path: str) -> str:
        url = (
            "https://raw.githubusercontent.com/"
            f"{full_name}/{urllib.parse.quote(default_branch, safe='')}/{urllib.parse.quote(path, safe='/')}"
        )
        return http_get_text(url, headers={"User-Agent": "sure-feed-online-discover"}, timeout=self.timeout, max_bytes=30_000)

    def _lang_for_path(self, path: str) -> str:
        lower = path.lower()
        if lower.endswith(".py"):
            return "python"
        if lower.endswith(".sh"):
            return "bash"
        if lower.endswith((".yml", ".yaml")):
            return "yaml"
        if lower.endswith(".md"):
            return "markdown"
        return "text"

    def _repo_details(self, full_name: str) -> dict[str, Any]:
        try:
            payload = http_get_json(f"{GITHUB_API}/repos/{full_name}", headers=self._headers(), timeout=self.timeout)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _readme(self, full_name: str) -> str:
        return http_get_text(
            f"{GITHUB_API}/repos/{full_name}/readme",
            headers=self._headers(raw=True),
            timeout=self.timeout,
            max_bytes=80_000,
        )

    def _latest_release(self, full_name: str) -> dict[str, Any] | None:
        try:
            payload = http_get_json(
                f"{GITHUB_API}/repos/{full_name}/releases/latest",
                headers=self._headers(),
                timeout=self.timeout,
            )
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _infer_weights_source(self, readme: str, release: dict[str, Any] | None) -> tuple[str, str]:
        if release and release.get("assets"):
            return "release_or_pypi", "latest GitHub release has assets"
        lowered = readme.lower()
        if "huggingface.co/" in lowered:
            return "huggingface", "README references HuggingFace"
        if "modelscope.cn/" in lowered:
            return "modelscope", "README references ModelScope"
        if re.search(r"\bpip install\b", lowered):
            return "pip", "README documents pip install"
        return "release_or_pypi", "weak fallback: GitHub code source without explicit remote weights"
