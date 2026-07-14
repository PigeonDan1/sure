from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resource_key(candidate: dict[str, Any]) -> str:
    provider = str(candidate.get("provider", "modelscope"))
    resource_type = str(candidate.get("resource_type", "unknown"))
    resource_id = str(candidate.get("resource_id") or candidate.get("id") or "")
    if not resource_id:
        raise ValueError("candidate missing resource_id")
    return f"{provider}:{resource_type}:{resource_id}"


class XForgeCatalog:
    """Persistent resource catalog for daily watcher de-duplication."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "resources": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def get(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        return self.data["resources"].get(resource_key(candidate))

    def upsert_candidate(self, candidate: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        key = resource_key(candidate)
        now = utc_now()
        resources = self.data.setdefault("resources", {})
        existing = resources.get(key)
        if existing:
            existing["last_seen_at"] = now
            existing["seen_count"] = int(existing.get("seen_count", 1)) + 1
            existing["latest_candidate"] = candidate
            self.save()
            return False, existing

        record = {
            "key": key,
            "status": "discovered",
            "first_seen_at": now,
            "last_seen_at": now,
            "seen_count": 1,
            "latest_candidate": candidate,
            "artifacts": {},
        }
        resources[key] = record
        self.save()
        return True, record

    def attach_artifact(self, candidate: dict[str, Any], name: str, path: str | Path) -> None:
        key = resource_key(candidate)
        record = self.data.setdefault("resources", {}).setdefault(key, {})
        record.setdefault("artifacts", {})[name] = str(Path(path))
        record["last_artifact_at"] = utc_now()
        self.save()

    def set_status(self, candidate: dict[str, Any], status: str) -> None:
        key = resource_key(candidate)
        record = self.data.setdefault("resources", {}).setdefault(key, {})
        record["status"] = status
        record["status_updated_at"] = utc_now()
        self.save()
