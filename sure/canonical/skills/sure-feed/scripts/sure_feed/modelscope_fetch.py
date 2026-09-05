from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sure_feed.bridge import plan_dataset_integration, plan_model_integration
from sure_feed.modelscope_watcher import (
    _dataset_manifest,
    _handoff_event,
    _model_manifest,
    slugify,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_selected_candidate(
    resource_type: str,
    task: str,
    resource_id: str,
    name: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    if resource_type not in ("model", "dataset"):
        raise ValueError("resource_type must be 'model' or 'dataset'")
    return {
        "resource_type": resource_type,
        "provider": "modelscope",
        "resource_id": resource_id,
        "name": name or resource_id.split("/")[-1],
        "task": task,
        "language": language,
        "url": f"https://modelscope.cn/{resource_type}s/{resource_id}",
        "selected_at": _utc_now(),
    }


def emit_selected_resource_artifacts(
    candidate: dict[str, Any],
    manifest_dir: str | Path,
    handoff_dir: str | Path,
) -> dict[str, str]:
    manifest_root = Path(manifest_dir)
    handoff_root = Path(handoff_dir)
    resource_type = str(candidate["resource_type"])
    stem = slugify(str(candidate["resource_id"]))
    manifest_path = manifest_root / f"{stem}.{resource_type}.json"
    if resource_type == "model":
        manifest = _model_manifest(candidate)
    else:
        manifest = _dataset_manifest(candidate, manifest_root)
    _write_json(manifest_path, manifest)
    handoff_path = handoff_root / f"{stem}.handoff.json"
    _write_json(handoff_path, _handoff_event(candidate, manifest_path))
    return {"manifest_path": str(manifest_path), "handoff_path": str(handoff_path)}


def emit_sure_integration_plan(
    resource_type: str,
    manifest: dict[str, Any],
    manifest_path: str | Path,
    handoff_path: str | Path,
    sure_plan_dir: str | Path,
    model_dir: str | Path,
    sure_dataset_dir: str | Path,
) -> str:
    plan_root = Path(sure_plan_dir)
    resource_id = str(manifest["source"]["id"] if resource_type == "model" else manifest["dataset_id"])
    plan_path = plan_root / f"{slugify(resource_id)}.sure_plan.json"
    if resource_type == "model":
        plan = plan_model_integration(
            manifest=manifest,
            manifest_path=manifest_path,
            handoff_path=handoff_path,
            model_dir=model_dir,
        )
    elif resource_type == "dataset":
        plan = plan_dataset_integration(
            manifest=manifest,
            manifest_path=manifest_path,
            handoff_path=handoff_path,
            sure_dataset_dir=sure_dataset_dir,
        )
    else:
        raise ValueError("resource_type must be 'model' or 'dataset'")
    _write_json(plan_path, plan)
    return str(plan_path)


def write_fetch_success(fetch_run_dir: str | Path, payload: dict[str, Any]) -> str:
    fetch_root = Path(fetch_run_dir)
    stem = slugify(str(payload["resource_id"]))
    path = fetch_root / f"{stem}.success.json"
    _write_json(path, {"status": "succeeded", "created_at": _utc_now(), **payload})
    return str(path)


def write_fetch_failure(
    fetch_run_dir: str | Path,
    resource_type: str,
    task: str,
    resource_id: str,
    command: list[str],
    error: str,
) -> str:
    fetch_root = Path(fetch_run_dir)
    stem = slugify(resource_id)
    path = fetch_root / f"{stem}.failed.json"
    _write_json(
        path,
        {
            "status": "failed",
            "provider": "modelscope",
            "resource_type": resource_type,
            "task": task,
            "resource_id": resource_id,
            "command": command,
            "error": error,
            "created_at": _utc_now(),
        },
    )
    return str(path)
