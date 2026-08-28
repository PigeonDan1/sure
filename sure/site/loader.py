#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import yaml

SITE_POLICY_ENV = "SURE_SITE_POLICY"
SITE_POLICY_SCHEMA = "sure.site.policy.v1"
MISSING_POLICY_MESSAGE = (
    "SURE site policy is not configured.\n"
    "Missing: config/site.bundled.yaml (bundled distribution) or config/site.local.yaml (local configuration).\n"
    "Fix: cp config/site.example.yaml config/site.local.yaml and edit the model, result, dataset and runtime paths.\n"
    "Verify: npm run sure:site-check\n"
    "See README.md#publicself-hosted-site-policy and docs/site-configuration.md."
)


class SitePolicyError(ValueError):
    pass


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SitePolicyError(f"{location} must be a mapping")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise SitePolicyError(f"{location} has unknown field: {unknown[0]}")


def _string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise SitePolicyError(f"{location} must be a non-empty string")
    return value


def _absolute_path(value: Any, location: str) -> str:
    path = _string(value, location)
    if not Path(path).is_absolute():
        raise SitePolicyError(f"{location} must be an absolute path")
    return path


def _unique_strings(value: Any, location: str, *, absolute: bool) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SitePolicyError(f"{location} must be a non-empty list")
    if absolute and len(value) != 1:
        raise SitePolicyError(f"{location} must contain exactly one path in policy v1")
    parser = _absolute_path if absolute else _string
    items = [parser(item, f"{location}[{index}]") for index, item in enumerate(value)]
    if len(set(items)) != len(items):
        raise SitePolicyError(f"{location} must not contain duplicates")
    return items


def validate_site_policy(value: Any) -> dict[str, Any]:
    root = _mapping(value, "site policy")
    _reject_unknown(root, {"schema", "site_id", "policy_version", "storage", "datasets", "execution", "network"}, "site policy")
    if root.get("schema") != SITE_POLICY_SCHEMA:
        raise SitePolicyError(f"schema must be {SITE_POLICY_SCHEMA}")
    site_id = _string(root.get("site_id"), "site_id")
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", site_id) is None:
        raise SitePolicyError("site_id has an invalid format")
    if root.get("policy_version") != 1:
        raise SitePolicyError("policy_version must be 1")

    storage = _mapping(root.get("storage"), "storage")
    _reject_unknown(storage, {"approved_models_roots", "approved_results_roots", "forbidden_output_roots", "runtime_root"}, "storage")
    datasets = _mapping(root.get("datasets"), "datasets")
    _reject_unknown(datasets, {"allowed_source_roots"}, "datasets")
    execution = _mapping(root.get("execution"), "execution")
    _reject_unknown(execution, {"surfaces", "local_runtimes", "vc_partitions", "vc_partition_priority"}, "execution")
    surfaces = _unique_strings(execution.get("surfaces"), "execution.surfaces", absolute=False)
    if any(surface not in {"local", "vc"} for surface in surfaces):
        raise SitePolicyError("execution.surfaces contains an unsupported value")
    local_runtimes = _unique_strings(
        execution.get("local_runtimes", ["container"]),
        "execution.local_runtimes",
        absolute=False,
    )
    if any(runtime not in {"container", "python"} for runtime in local_runtimes):
        raise SitePolicyError("execution.local_runtimes contains an unsupported value")

    policy: dict[str, Any] = {
        "schema": SITE_POLICY_SCHEMA,
        "site_id": site_id,
        "policy_version": 1,
        "storage": {
            "approved_models_roots": _unique_strings(storage.get("approved_models_roots"), "storage.approved_models_roots", absolute=True),
            "approved_results_roots": _unique_strings(storage.get("approved_results_roots"), "storage.approved_results_roots", absolute=True),
            "forbidden_output_roots": _unique_strings(storage.get("forbidden_output_roots"), "storage.forbidden_output_roots", absolute=True),
            "runtime_root": _absolute_path(storage.get("runtime_root"), "storage.runtime_root"),
        },
        "datasets": {
            "allowed_source_roots": _unique_strings(datasets.get("allowed_source_roots"), "datasets.allowed_source_roots", absolute=True),
        },
        "execution": {"surfaces": surfaces, "local_runtimes": local_runtimes},
    }
    if "vc_partitions" in execution:
        policy["execution"]["vc_partitions"] = _unique_strings(execution["vc_partitions"], "execution.vc_partitions", absolute=False)
    if "vc_partition_priority" in execution:
        priority = _mapping(execution["vc_partition_priority"], "execution.vc_partition_priority")
        parsed_priority: dict[str, int] = {}
        for name, value in priority.items():
            if not isinstance(name, str) or re.fullmatch(r"\S+", name) is None or not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise SitePolicyError(f"execution.vc_partition_priority.{name} must be a non-negative integer")
            parsed_priority[name] = value
        policy["execution"]["vc_partition_priority"] = parsed_priority
    if "network" in root:
        source = _mapping(root["network"], "network")
        _reject_unknown(source, {"internal_git_host", "gateway_portal"}, "network")
        network = {}
        if "internal_git_host" in source:
            network["internal_git_host"] = _string(source["internal_git_host"], "network.internal_git_host")
        if "gateway_portal" in source:
            portal = _string(source["gateway_portal"], "network.gateway_portal")
            parsed_portal = urlparse(portal)
            if parsed_portal.scheme not in {"http", "https"} or not parsed_portal.netloc:
                raise SitePolicyError("network.gateway_portal must be a valid HTTP(S) URL")
            network["gateway_portal"] = portal
        policy["network"] = network
    return policy


def load_site_policy(
    repository_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
    *,
    required: bool = False,
) -> dict[str, Any] | None:
    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    env = environment if environment is not None else os.environ
    explicit = env.get(SITE_POLICY_ENV, "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            raise SitePolicyError(f"{SITE_POLICY_ENV} must be an absolute path")
        return _load(path.resolve(), "environment")
    for path, source in (
        (root / "config" / "site.bundled.yaml", "bundled"),
        (root / "config" / "site.local.yaml", "local"),
    ):
        if path.exists():
            return _load(path, source)
    if required:
        raise SitePolicyError(MISSING_POLICY_MESSAGE)
    return None


def _load(path: Path, source: str) -> dict[str, Any]:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise SitePolicyError(f"Cannot read {source} site policy {path}: {error}") from error
    try:
        decoded = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise SitePolicyError(f"Cannot parse {source} site policy {path}: {error}") from error
    try:
        policy = validate_site_policy(decoded)
    except SitePolicyError as error:
        raise SitePolicyError(f"Invalid {source} site policy {path}: {error}") from error
    return {
        "policy": policy,
        "path": str(path),
        "source": source,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


if __name__ == "__main__":
    try:
        resolved = load_site_policy(required=True)
    except SitePolicyError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(resolved, ensure_ascii=False, sort_keys=True))
