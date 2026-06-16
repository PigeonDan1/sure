from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from xforge_sure_bridge.catalog import XForgeCatalog, resource_key, utc_now
from xforge_sure_bridge.modelscope_daily import modelscope_task_queries, task_match_score


DEFAULT_MODELSCOPE_API_BASE = "https://modelscope.cn/openapi/v1"
MAX_MODELSCOPE_OPENAPI_PAGE_SIZE = 50


def slugify(value: str) -> str:
    value = value.strip().replace("/", "__")
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return value.strip("._-") or "resource"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _extract_items(payload: Any, resource_type: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (
        resource_type,
        f"{resource_type}s",
        resource_type.title(),
        f"{resource_type}s".title(),
        "items",
        "data",
        "Data",
        "list",
        "models",
        "Models",
        "datasets",
        "Datasets",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_items(value, resource_type)
            if nested:
                return nested
    return []


def _normalize_candidate(raw: dict[str, Any], resource_type: str, task: str) -> dict[str, Any]:
    resource_id = (
        raw.get("resource_id")
        or raw.get("modelId")
        or raw.get("model_id")
        or raw.get("datasetId")
        or raw.get("dataset_id")
        or raw.get("id")
        or raw.get("name")
        or raw.get("Path")
    )
    if not resource_id:
        raise ValueError(f"cannot determine resource id for {resource_type}: {raw}")
    name = raw.get("display_name") or raw.get("name") or raw.get("Name") or str(resource_id).split("/")[-1]
    candidate_task = raw.get("task") or raw.get("pipeline_tag") or raw.get("tasks") or ""
    return {
        "resource_type": resource_type,
        "provider": "modelscope",
        "resource_id": str(resource_id),
        "name": str(name),
        "task": candidate_task if isinstance(candidate_task, list) else str(candidate_task),
        "language": raw.get("language") or raw.get("lang"),
        "license": raw.get("license"),
        "description": raw.get("description") or raw.get("summary"),
        "updated_at": raw.get("updated_at") or raw.get("last_modified") or raw.get("lastModified") or raw.get("gmtModified"),
        "downloads": raw.get("downloads") or raw.get("download_count") or raw.get("downloadCount"),
        "tags": raw.get("tags"),
        "url": raw.get("url") or f"https://modelscope.cn/{resource_type}s/{resource_id}",
        "raw": raw,
    }


class ModelScopeWatcher:
    """Small ModelScope watcher with injectable API parameters."""

    def __init__(self, api_base: str = DEFAULT_MODELSCOPE_API_BASE) -> None:
        self.api_base = api_base.rstrip("/")

    def _request_json(self, endpoint: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def search(
        self,
        task: str,
        resource_types: list[str],
        since_days: int,
        max_items: int,
        extra_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen_resource_ids: set[tuple[str, str]] = set()
        since = datetime.now(timezone.utc) - timedelta(days=since_days)

        for resource_type in resource_types:
            endpoint = "models" if resource_type == "model" else "datasets"
            for query in modelscope_task_queries(task, resource_type):
                for sort in self._search_sorts(query):
                    fetched = 0
                    page = 1
                    while fetched < max_items:
                        params = self._search_params(
                            query=query,
                            max_items=max_items,
                            page=page,
                            sort=sort,
                        )
                        if extra_params:
                            params.update(extra_params)
                        payload = self._request_json(endpoint, params)
                        items = _extract_items(payload, resource_type)
                        if not items:
                            break
                        fetched += len(items)
                        for raw in items:
                            candidate = _normalize_candidate(raw, resource_type, task)
                            resource_key_tuple = (resource_type, str(candidate["resource_id"]))
                            if resource_key_tuple in seen_resource_ids:
                                continue
                            api_params = dict(query["api_params"])
                            if sort:
                                api_params["sort"] = sort
                            candidate["acquisition_filter"] = {
                                "api_base": self.api_base,
                                "endpoint": endpoint,
                                "api_params": api_params,
                                "ui_params": query["ui_params"],
                                "match_source": query["match_source"],
                                "effective_sort": sort,
                            }
                            if _is_recent_enough(candidate.get("updated_at"), since) and task_match_score(candidate, task) > 0:
                                candidates.append(candidate)
                                seen_resource_ids.add(resource_key_tuple)
                        if not self._uses_openapi():
                            break
                        page += 1
        return candidates

    def _uses_openapi(self) -> bool:
        return "/openapi/" in self.api_base

    def _search_params(
        self,
        query: dict[str, Any],
        max_items: int,
        page: int,
        sort: str | None = None,
    ) -> dict[str, Any]:
        if self._uses_openapi():
            params = {
                "page_size": min(max_items, MAX_MODELSCOPE_OPENAPI_PAGE_SIZE),
                "page_number": page,
            }
            params.update(query["api_params"])
            if sort:
                params["sort"] = sort
            return params
        return {"pageSize": max_items, "sort": "updated_at", **query["api_params"]}

    def _search_sorts(self, query: dict[str, Any]) -> list[str | None]:
        if not self._uses_openapi():
            return [None]
        base_sort = query["api_params"].get("sort")
        sorts: list[str | None] = [base_sort or "last_modified", "downloads"]
        unique: list[str | None] = []
        for sort in sorts:
            if sort not in unique:
                unique.append(sort)
        return unique


def _is_recent_enough(updated_at: Any, since: datetime) -> bool:
    if not updated_at:
        return True
    value = str(updated_at).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= since


def _model_manifest(candidate: dict[str, Any]) -> dict[str, Any]:
    model_name = slugify(str(candidate.get("name") or candidate["resource_id"]))
    return {
        "resource_type": "model",
        "model_name": model_name,
        "task_type": str(candidate.get("task", "asr")).lower(),
        "source": {
            "provider": "modelscope",
            "id": str(candidate["resource_id"]),
        },
        "discovery": candidate,
    }


def _dataset_manifest(candidate: dict[str, Any], manifest_dir: Path) -> dict[str, Any]:
    sure_name = slugify(str(candidate.get("name") or candidate["resource_id"]))
    raw_root = Path("data/datasets/xforge_raw") / sure_name
    dataset_manifest = {
        "resource_type": "dataset",
        "dataset_id": str(candidate["resource_id"]),
        "sure_name": sure_name,
        "task": str(candidate.get("task") or "ASR").upper(),
        "language": candidate.get("language") or "auto",
        "raw_root": str(raw_root),
        "raw_jsonl": "",
        "field_mapping": {},
        "source": {
            "provider": "modelscope",
            "id": str(candidate["resource_id"]),
        },
        "bridge_ready": False,
        "processing_status": "requires_dataset_schema_mapping",
        "discovery": candidate,
    }
    return dataset_manifest


def _handoff_event(candidate: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    if candidate["resource_type"] == "model":
        return {
            "event_type": "xforge_model_discovered",
            "target_agent": "sure_tool_agent",
            "next_state": "FETCH_WEIGHTS",
            "status": "ready_for_model_collect",
            "manifest_path": str(manifest_path),
            "resource_key": resource_key(candidate),
            "created_at": utc_now(),
        }
    return {
        "event_type": "xforge_dataset_discovered",
        "target_agent": "sure_main_agent",
        "next_state": "DATASET_SCOPE_UNIT",
        "status": "blocked_until_dataset_schema_mapping",
        "manifest_path": str(manifest_path),
        "resource_key": resource_key(candidate),
        "created_at": utc_now(),
    }


def process_candidates(
    candidates: list[dict[str, Any]],
    catalog: XForgeCatalog,
    manifest_dir: str | Path,
    handoff_dir: str | Path,
    emit_manifests: bool,
    emit_handoff: bool,
) -> dict[str, Any]:
    manifest_root = Path(manifest_dir)
    handoff_root = Path(handoff_dir)
    emitted_manifests: list[dict[str, str]] = []
    emitted_handoffs: list[dict[str, str]] = []
    new_count = 0

    for candidate in candidates:
        is_new, _record = catalog.upsert_candidate(candidate)
        if not is_new:
            continue
        new_count += 1

        if not emit_manifests:
            continue

        resource_type = candidate["resource_type"]
        stem = slugify(str(candidate["resource_id"]))
        manifest_path = manifest_root / f"{stem}.{resource_type}.json"
        manifest = _model_manifest(candidate) if resource_type == "model" else _dataset_manifest(candidate, manifest_root)
        _write_json(manifest_path, manifest)
        catalog.attach_artifact(candidate, "bridge_manifest", manifest_path)
        emitted_manifests.append({"resource_key": resource_key(candidate), "path": str(manifest_path)})

        if emit_handoff:
            handoff_path = handoff_root / f"{stem}.handoff.json"
            handoff = _handoff_event(candidate, manifest_path)
            _write_json(handoff_path, handoff)
            catalog.attach_artifact(candidate, "handoff", handoff_path)
            emitted_handoffs.append({"resource_key": resource_key(candidate), "path": str(handoff_path)})

    return {
        "new_count": new_count,
        "candidate_count": len(candidates),
        "manifests": emitted_manifests,
        "handoffs": emitted_handoffs,
    }
