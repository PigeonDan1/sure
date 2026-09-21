"""What a mounted model bundle is called.

Inside the container the bundle sits under a policy-defined mount such as
/workspace/model, so the directory name is an alias. The sealed
runtime_inventory.json is the approved record and names the model; config.yaml
is whatever the wrapper author wrote and comes next; the directory is the last
resort. Every writer of a model name (generation status, prediction manifest,
protocol.yaml) goes through here so they cannot drift apart, and
finalize_result_bundle checks them against the same inventory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def canonical_model_name(model_dir: Path, config: dict[str, Any] | None = None) -> str:
    """runtime_inventory.model.name, else the config's name, else the directory name."""
    inventory = _read_json(model_dir / "artifacts" / "runtime_inventory.json")
    sealed = inventory.get("model") if isinstance(inventory.get("model"), dict) else {}
    if config is None:
        config = _read_yaml(model_dir / "config.yaml")
    nested = config.get("model") if isinstance(config.get("model"), dict) else {}
    for candidate in (sealed.get("name"), config.get("name"), nested.get("name")):
        if isinstance(candidate, str) and candidate:
            return candidate
    return model_dir.name
