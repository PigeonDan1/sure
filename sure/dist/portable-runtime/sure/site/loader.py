#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlparse

import yaml

if __package__:
    from .container_delivery import ContainerDeliveryError, validate_repository_template
else:
    from container_delivery import ContainerDeliveryError, validate_repository_template

SITE_POLICY_ENV = "SURE_SITE_POLICY"
SITE_POLICY_SNAPSHOT_ENV = "SURE_SITE_POLICY_SNAPSHOT"
POLICY_DIGEST_ENV = "SURE_POLICY_DIGEST"
POLICY_SNAPSHOT_DIGEST_ENV = "SURE_POLICY_SNAPSHOT_DIGEST"
SITE_POLICY_SCHEMA = "sure.site.policy.v1"
POLICY_SNAPSHOT_SCHEMA = "sure.policy.snapshot.v1"
POLICY_PATH_ROLES = {
    "read_only_reference",
    "controlled_publication",
    "dataset_source",
    "runtime_cache",
    "forbidden_output",
}
DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
ROOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
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
    # Site policy paths are POSIX cluster paths; policy.schema.json declares
    # them as "^/". Path() would answer by host platform, so a Windows host
    # rejected the site's own roots and accepted drive-letter paths.
    path = _string(value, location)
    if not PurePosixPath(path).is_absolute():
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


def _source_roots(value: Any, location: str) -> dict[str, str]:
    # Support legacy single-path array format: [/path] → { "default": "/path" }
    if isinstance(value, list):
        if not value:
            raise SitePolicyError(f"{location} must contain at least one entry")
        if len(value) != 1:
            raise SitePolicyError(f"{location} must contain exactly one path in policy v1 (or use key-value format)")
        path = _absolute_path(value[0], f"{location}[0]")
        return {"default": path}
    if not isinstance(value, dict):
        raise SitePolicyError(f"{location} must be a mapping")
    result: dict[str, str] = {}
    paths: set[str] = set()
    for key, val in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", key):
            raise SitePolicyError(f"{location} key \"{key}\" must match pattern [a-z0-9][a-z0-9._-]*")
        path = _absolute_path(val, f"{location}.{key}")
        if path in paths:
            raise SitePolicyError(f"{location} must not contain duplicate paths")
        paths.add(path)
        result[key] = path
    if not result:
        raise SitePolicyError(f"{location} must contain at least one entry")
    return result


def _canonical_digest(value: Any) -> str:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _normalized_digest(value: Any, location: str) -> str:
    digest = _string(value, location)
    if DIGEST_RE.fullmatch(digest) is None:
        raise SitePolicyError(f"{location} must be SHA-256")
    return digest if digest.startswith("sha256:") else f"sha256:{digest}"


def _normalized_snapshot_path(value: Any, location: str) -> str:
    path = _absolute_path(value, location)
    if posixpath.normpath(path) != path:
        raise SitePolicyError(f"{location} must be normalized")
    return path


def _snapshot_binding(value: Any, index: int) -> dict[str, str]:
    location = f"policy snapshot path_bindings[{index}]"
    binding = _mapping(value, location)
    _reject_unknown(binding, {"root_id", "role", "path", "resolved_path"}, location)
    root_id = _string(binding.get("root_id"), f"{location}.root_id")
    if ROOT_ID_RE.fullmatch(root_id) is None:
        raise SitePolicyError(f"{location}.root_id has an invalid format")
    role = _string(binding.get("role"), f"{location}.role")
    if role not in POLICY_PATH_ROLES:
        raise SitePolicyError(f"{location}.role is invalid")
    return {
        "root_id": root_id,
        "role": role,
        "path": _normalized_snapshot_path(binding.get("path"), f"{location}.path"),
        "resolved_path": _normalized_snapshot_path(
            binding.get("resolved_path"),
            f"{location}.resolved_path",
        ),
    }


def validate_site_policy(value: Any) -> dict[str, Any]:
    root = _mapping(value, "site policy")
    _reject_unknown(
        root,
        {"schema", "site_id", "policy_version", "storage", "datasets", "execution", "network", "container_delivery"},
        "site policy",
    )
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
    _reject_unknown(datasets, {"allowed_source_roots", "projection_root"}, "datasets")
    execution = _mapping(root.get("execution"), "execution")
    _reject_unknown(
        execution,
        {"surfaces", "local_runtimes", "vc_project", "vc_partitions", "vc_partition_priority", "vc_default_partition"},
        "execution",
    )
    surfaces = _unique_strings(execution.get("surfaces"), "execution.surfaces", absolute=False)
    if any(surface not in {"local", "vc"} for surface in surfaces):
        raise SitePolicyError("execution.surfaces contains an unsupported value")
    local_runtimes = _unique_strings(
        execution.get("local_runtimes", ["container"]),
        "execution.local_runtimes",
        absolute=False,
    )
    if any(runtime not in {"python", "container"} for runtime in local_runtimes):
        raise SitePolicyError("execution.local_runtimes contains an unsupported value")

    policy: dict[str, Any] = {
        "schema": SITE_POLICY_SCHEMA,
        "site_id": site_id,
        "policy_version": 1,
        "storage": {
            "approved_models_roots": _unique_strings(storage.get("approved_models_roots"), "storage.approved_models_roots", absolute=True),
            "approved_results_roots": (
                _unique_strings(storage["approved_results_roots"], "storage.approved_results_roots", absolute=True)
                if "approved_results_roots" in storage
                else []
            ),
            "forbidden_output_roots": _unique_strings(storage.get("forbidden_output_roots"), "storage.forbidden_output_roots", absolute=True),
            "runtime_root": _absolute_path(storage.get("runtime_root"), "storage.runtime_root"),
        },
        "datasets": {
            "allowed_source_roots": _source_roots(datasets.get("allowed_source_roots"), "datasets.allowed_source_roots"),
        },
        "execution": {"surfaces": surfaces, "local_runtimes": local_runtimes},
    }
    if "projection_root" in datasets:
        policy["datasets"]["projection_root"] = _absolute_path(
            datasets["projection_root"], "datasets.projection_root"
        )
    if "vc_project" in execution:
        policy["execution"]["vc_project"] = _string(
            execution["vc_project"], "execution.vc_project"
        )
    if "vc" in surfaces and "vc_project" not in policy["execution"]:
        raise SitePolicyError(
            "execution.vc_project is required when the vc surface is enabled"
        )
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
    if "vc_default_partition" in execution:
        default_partition = _string(execution["vc_default_partition"], "execution.vc_default_partition")
        allowed_partitions = policy["execution"].get("vc_partitions")
        if allowed_partitions is not None and default_partition not in allowed_partitions:
            raise SitePolicyError("execution.vc_default_partition must be listed in execution.vc_partitions")
        policy["execution"]["vc_default_partition"] = default_partition
    if "network" in root:
        source = _mapping(root["network"], "network")
        _reject_unknown(source, {"internal_git_host", "gateway_portal", "container_registry"}, "network")
        network = {}
        if "internal_git_host" in source:
            network["internal_git_host"] = _string(source["internal_git_host"], "network.internal_git_host")
        if "container_registry" in source:
            network["container_registry"] = _string(source["container_registry"], "network.container_registry")
        if "gateway_portal" in source:
            portal = _string(source["gateway_portal"], "network.gateway_portal")
            parsed_portal = urlparse(portal)
            if parsed_portal.scheme not in {"http", "https"} or not parsed_portal.netloc:
                raise SitePolicyError("network.gateway_portal must be a valid HTTP(S) URL")
            network["gateway_portal"] = portal
        policy["network"] = network
    if "container_delivery" in root:
        delivery_source = _mapping(root["container_delivery"], "container_delivery")
        _reject_unknown(delivery_source, {"repository_template"}, "container_delivery")
        if not policy.get("network", {}).get("container_registry"):
            raise SitePolicyError(
                "container_delivery.repository_template requires network.container_registry"
            )
        try:
            repository_template = validate_repository_template(delivery_source.get("repository_template"))
        except ContainerDeliveryError as error:
            raise SitePolicyError(f"container_delivery.{error}") from error
        policy["container_delivery"] = {"repository_template": repository_template}
    return policy


def load_site_policy(
    repository_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
    *,
    required: bool = False,
) -> dict[str, Any] | None:
    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    env = environment if environment is not None else os.environ
    snapshot = env.get(SITE_POLICY_SNAPSHOT_ENV, "").strip()
    if snapshot:
        path = Path(snapshot)
        if not path.is_absolute():
            raise SitePolicyError(f"{SITE_POLICY_SNAPSHOT_ENV} must be an absolute path")
        if path.is_symlink():
            raise SitePolicyError(f"{SITE_POLICY_SNAPSHOT_ENV} must not be a symlink")
        resolved = _load_snapshot(path.resolve())
        for variable, field in (
            (POLICY_DIGEST_ENV, "policy_digest"),
            (POLICY_SNAPSHOT_DIGEST_ENV, "snapshot_digest"),
        ):
            expected = env.get(variable, "").strip()
            if expected and _normalized_digest(expected, variable) != resolved[field]:
                raise SitePolicyError(
                    f"site policy snapshot {field} does not match the run binding"
                )
        return resolved
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


def _load_snapshot(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SitePolicyError(f"Cannot read site policy snapshot {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise SitePolicyError(f"Cannot parse site policy snapshot {path}: {error}") from error

    root = _mapping(decoded, "policy snapshot")
    _reject_unknown(
        root,
        {
            "schema",
            "site_id",
            "policy_version",
            "policy",
            "source",
            "path_bindings",
            "policy_digest",
            "bindings_digest",
            "snapshot_digest",
        },
        "policy snapshot",
    )
    if root.get("schema") != POLICY_SNAPSHOT_SCHEMA:
        raise SitePolicyError(f"policy snapshot schema must be {POLICY_SNAPSHOT_SCHEMA}")

    policy = validate_site_policy(root.get("policy"))
    if root.get("policy") != policy:
        raise SitePolicyError("policy snapshot policy is not canonically normalized")
    if root.get("site_id") != policy["site_id"]:
        raise SitePolicyError("policy snapshot site_id does not match its policy")
    if root.get("policy_version") != policy["policy_version"]:
        raise SitePolicyError("policy snapshot policy_version does not match its policy")

    source = _mapping(root.get("source"), "policy snapshot source")
    _reject_unknown(source, {"kind", "path", "raw_sha256"}, "policy snapshot source")
    source_kind = _string(source.get("kind"), "policy snapshot source.kind")
    raw_sha256 = _normalized_digest(
        source.get("raw_sha256"),
        "policy snapshot source.raw_sha256",
    )
    normalized_source: dict[str, str] = {
        "kind": source_kind,
        "raw_sha256": raw_sha256,
    }
    if "path" in source:
        normalized_source["path"] = _normalized_snapshot_path(
            source.get("path"),
            "policy snapshot source.path",
        )

    raw_bindings = root.get("path_bindings")
    if not isinstance(raw_bindings, list):
        raise SitePolicyError("policy snapshot path_bindings must be a list")
    bindings = [_snapshot_binding(value, index) for index, value in enumerate(raw_bindings)]
    root_ids = [binding["root_id"] for binding in bindings]
    if len(set(root_ids)) != len(root_ids):
        raise SitePolicyError("policy snapshot path_bindings contains a duplicate root_id")
    bindings.sort(key=lambda binding: binding["root_id"])

    policy_digest = _canonical_digest(policy)
    bindings_digest = _canonical_digest(bindings)
    payload = {
        "schema": POLICY_SNAPSHOT_SCHEMA,
        "site_id": policy["site_id"],
        "policy_version": policy["policy_version"],
        "policy": policy,
        "source": normalized_source,
        "path_bindings": bindings,
        "policy_digest": policy_digest,
        "bindings_digest": bindings_digest,
    }
    snapshot_digest = _canonical_digest(payload)
    for field, expected in (
        ("policy_digest", policy_digest),
        ("bindings_digest", bindings_digest),
        ("snapshot_digest", snapshot_digest),
    ):
        supplied = _normalized_digest(root.get(field), f"policy snapshot {field}")
        if supplied != expected:
            raise SitePolicyError(
                f"policy snapshot {field} does not match its canonical contents"
            )

    return {
        "policy": policy,
        "path": normalized_source.get("path", str(path)),
        "source": source_kind,
        "sha256": raw_sha256.removeprefix("sha256:"),
        "policy_digest": policy_digest,
        "bindings_digest": bindings_digest,
        "snapshot_digest": snapshot_digest,
        "snapshot_path": str(path),
        "path_bindings": bindings,
    }


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
