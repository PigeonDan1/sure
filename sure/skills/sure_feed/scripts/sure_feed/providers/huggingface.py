from __future__ import annotations

import urllib.parse
from typing import Any

from sure_feed.providers.base import ProviderNetworkError, ProviderRequest, append_query, http_get_json, http_get_text


PRIMARY_ENDPOINT = "https://huggingface.co"
MIRROR_ENDPOINT = "https://hf-mirror.com"


class HuggingFaceProvider:
    source = "huggingface"

    def __init__(self, endpoint_mode: str = "auto", timeout: int = 20) -> None:
        self.endpoint_mode = endpoint_mode
        self.timeout = timeout
        self.last_endpoint_used: str | None = None
        self.fallback_reason: str | None = None

    def _candidate_endpoints(self) -> list[str]:
        if self.endpoint_mode == "auto":
            return [PRIMARY_ENDPOINT, MIRROR_ENDPOINT]
        if self.endpoint_mode in {"huggingface", "primary"}:
            return [PRIMARY_ENDPOINT]
        if self.endpoint_mode in {"hf-mirror", "mirror"}:
            return [MIRROR_ENDPOINT]
        if self.endpoint_mode.startswith("http://") or self.endpoint_mode.startswith("https://"):
            return [self.endpoint_mode.rstrip("/")]
        raise ValueError(f"unsupported HuggingFace endpoint mode: {self.endpoint_mode}")

    def _get_json_with_fallback(self, path: str, params: dict[str, Any]) -> tuple[Any, str]:
        errors: list[str] = []
        headers = {"Accept": "application/json", "User-Agent": "sure-feed-online-discover"}
        for index, endpoint in enumerate(self._candidate_endpoints()):
            url = append_query(endpoint, path, params)
            try:
                payload = http_get_json(url, headers=headers, timeout=self.timeout)
                self.last_endpoint_used = endpoint
                if index > 0:
                    self.fallback_reason = "; ".join(errors)
                return payload, endpoint
            except ProviderNetworkError as exc:
                errors.append(str(exc))
                continue
        raise ProviderNetworkError("; ".join(errors) or "HuggingFace endpoint unavailable")

    def _get_text_with_fallback(self, path: str) -> tuple[str, str | None]:
        errors: list[str] = []
        headers = {"Accept": "text/plain,*/*", "User-Agent": "sure-feed-online-discover"}
        card_timeout = min(self.timeout, 15)
        for index, endpoint in enumerate(self._candidate_endpoints()):
            url = endpoint.rstrip("/") + "/" + path.lstrip("/")
            try:
                text = http_get_text(url, headers=headers, timeout=card_timeout, max_bytes=160_000)
            except ProviderNetworkError as exc:
                errors.append(str(exc))
                continue
            if text.strip():
                self.last_endpoint_used = endpoint
                if index > 0 and errors:
                    prior = [self.fallback_reason] if self.fallback_reason else []
                    self.fallback_reason = "; ".join(prior + errors)
                return text, endpoint
            errors.append(f"{url} returned an empty model card")
        return "", None

    def search(self, request: ProviderRequest) -> list[dict[str, Any]]:
        payload, endpoint = self._get_json_with_fallback(
            "/api/models",
            {"search": request.query or request.task, "limit": request.max_models},
        )
        items = payload if isinstance(payload, list) else []
        candidates: list[dict[str, Any]] = []
        for raw in items[: request.max_models]:
            if not isinstance(raw, dict):
                continue
            raw_model_id = str(raw.get("modelId") or raw.get("id") or "")
            card_text, card_endpoint = self._read_model_card(raw_model_id)
            candidate = self._candidate_from_raw(raw, card_endpoint or endpoint, model_card_text=card_text)
            if candidate:
                candidates.append(candidate)
        return candidates

    def direct(self, model_id: str) -> dict[str, Any]:
        path_model_id = urllib.parse.quote(model_id.strip("/"), safe="/")
        payload, endpoint = self._get_json_with_fallback(f"/api/models/{path_model_id}", {})
        if not isinstance(payload, dict):
            raise ValueError(f"HuggingFace model metadata is not an object: {model_id}")
        card_text, card_endpoint = self._read_model_card(model_id)
        candidate = self._candidate_from_raw(payload, card_endpoint or endpoint, model_id=model_id, model_card_text=card_text)
        if not candidate:
            raise ValueError(f"cannot normalize HuggingFace model metadata: {model_id}")
        return candidate

    def _read_model_card(self, model_id: str) -> tuple[str, str | None]:
        if not model_id:
            return "", None
        path_model_id = urllib.parse.quote(model_id.strip("/"), safe="/")
        return self._get_text_with_fallback(f"{path_model_id}/resolve/main/README.md")

    def _candidate_from_raw(
        self,
        raw: dict[str, Any],
        endpoint: str,
        model_id: str | None = None,
        model_card_text: str = "",
    ) -> dict[str, Any] | None:
        model_id = str(model_id or raw.get("modelId") or raw.get("id") or "")
        if not model_id:
            return None
        tags = [str(item) for item in raw.get("tags") or []]
        license_tag = next((tag.split("license:", 1)[1] for tag in tags if tag.startswith("license:")), None)
        canonical_repo = f"{PRIMARY_ENDPOINT}/{urllib.parse.quote(model_id, safe='/')}"
        card_data = raw.get("cardData") if isinstance(raw.get("cardData"), dict) else {}
        library_name = raw.get("library_name") or card_data.get("library_name")
        return {
            "source": self.source,
            "model_id": model_id,
            "repo": canonical_repo,
            "source_url": canonical_repo,
            "model_card_url": f"{canonical_repo}/blob/main/README.md",
            "model_card_text": model_card_text,
            "endpoint_used": endpoint,
            "endpoint_fallback_reason": self.fallback_reason,
            "tasks": [str(raw.get("pipeline_tag"))] if raw.get("pipeline_tag") else [],
            "pipeline_tag": raw.get("pipeline_tag"),
            "tags": tags,
            "description": raw.get("description") or card_data.get("summary"),
            "library_name": library_name,
            "license": raw.get("license") or license_tag,
            "download_count": raw.get("downloads"),
            "likes": raw.get("likes"),
            "created_at": raw.get("createdAt"),
            "commit": raw.get("sha"),
            "siblings": raw.get("siblings") or [],
            "weights_source": "huggingface",
            "raw": raw,
        }
