from __future__ import annotations

import json
import re
from typing import Any

from sure_feed.modelscope_watcher import DEFAULT_MODELSCOPE_API_BASE, ModelScopeWatcher
from sure_feed.providers.base import ProviderRequest, http_get_text


class ModelScopeProvider:
    source = "modelscope"

    def __init__(self, api_base: str = DEFAULT_MODELSCOPE_API_BASE, since_days: int = 3650) -> None:
        self.api_base = api_base
        self.since_days = since_days

    def search(self, request: ProviderRequest) -> list[dict[str, Any]]:
        watcher = ModelScopeWatcher(api_base=self.api_base)
        raw_candidates = watcher.search(
            task=request.task,
            resource_types=["model"],
            since_days=self.since_days,
            max_items=request.max_models,
            extra_params={"search": request.query} if request.query else None,
        )
        candidates: list[dict[str, Any]] = []
        for raw in raw_candidates[: request.max_models]:
            model_id = str(raw.get("resource_id") or "")
            if not model_id:
                continue
            task_value = raw.get("task")
            tasks = [str(item) for item in task_value] if isinstance(task_value, list) else [str(task_value)] if task_value else []
            candidates.append(
                self._candidate_from_raw(raw, model_id, tasks)
            )
        return candidates

    def direct(self, model_id: str, task_hint: str = "auto") -> dict[str, Any]:
        request = ProviderRequest(source=self.source, query=model_id, task=task_hint if task_hint != "auto" else model_id, max_models=10)
        for candidate in self.search(request):
            if candidate["model_id"] == model_id:
                return candidate
        return self._candidate_from_raw({}, model_id, [])

    def _candidate_from_raw(self, raw: dict[str, Any], model_id: str, tasks: list[str]) -> dict[str, Any]:
        repo = str(raw.get("url") or f"https://modelscope.cn/models/{model_id}")
        card_text = str(raw.get("readme") or raw.get("card") or raw.get("description") or "")
        if not card_text.strip():
            card_text = self._read_model_card(model_id, repo)
        return {
            "source": self.source,
            "model_id": model_id,
            "repo": repo,
            "source_url": repo,
            "model_card_url": repo,
            "model_card_text": card_text,
            "endpoint_used": self.api_base,
            "tasks": tasks,
            "pipeline_tag": None,
            "tags": [str(item) for item in raw.get("tags") or []],
            "description": raw.get("description"),
            "license": raw.get("license"),
            "download_count": raw.get("downloads"),
            "updated_at": raw.get("updated_at"),
            "weights_source": "modelscope",
            "raw": raw,
        }

    def _read_model_card(self, model_id: str, repo: str) -> str:
        for url in (
            f"https://modelscope.cn/models/{model_id}/summary",
            repo,
        ):
            text = http_get_text(url, headers={"User-Agent": "sure-feed-online-discover"}, timeout=20, max_bytes=120_000)
            parsed = self._extract_readme_from_html(text)
            if parsed:
                return parsed
            if text.strip() and "window.__detail_data__" not in text:
                return text
        return ""

    def _extract_readme_from_html(self, text: str) -> str:
        match = re.search(r'window\.__detail_data__ = "(.*?)";', text or "", re.S)
        if not match:
            return ""
        try:
            decoded = json.loads(f'"{match.group(1)}"')
            data = json.loads(decoded)
        except (json.JSONDecodeError, TypeError):
            return ""
        readme = data.get("ReadMeContent")
        return readme if isinstance(readme, str) else ""
