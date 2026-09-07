"""Host-neutral admission checks for external SURE execution adapters.

The module describes an adapter; it does not submit jobs or inspect a host.
The VC implementation remains in ``sure-trans/scripts/vc_exec.py``.  Keeping
this contract here lets a Python bridge and SURE Core reject the same malformed
or out-of-policy manifest before an adapter is registered.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

MANIFEST_SCHEMA = "sure.execution.adapter_manifest.v1"
ATTESTATION_MODES = {"none", "receipt_digest", "signed_receipt", "trusted_attestation"}
CANCELLATION_STRATEGIES = {"job_delete", "cooperative_signal", "none"}
CANCELLATION_CONFIRMATIONS = {"best_effort", "confirmed", "not_applicable"}
TIMEOUT_OUTCOMES = {"CANCELLED", "BLOCKED"}
WRITE_POLICIES = {"declared_outputs_only", "output_root"}
SURFACES = {"vc", "remote", "trusted"}
EXECUTOR_KINDS = {"local", "python", "docker", "remote", "trusted"}
TRUST_LEVELS = {"cooperative", "host_enforced", "attested"}
ALLOWED_EXECUTOR_KINDS = {
    "vc": {"remote", "trusted"},
    "remote": {"remote"},
    "trusted": {"trusted"},
}
DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RELATIVE_PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.?(?:/|$))(?!.*//)(?!.*\/$)(?!.*\\).+")
MAX_RESOURCE = 1_000_000
MAX_TIMEOUT_SECONDS = 604_800


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and bool(DIGEST_RE.fullmatch(value))


def _same_digest(left: str, right: str) -> bool:
    return left.removeprefix("sha256:").lower() == right.removeprefix("sha256:").lower()


def _normalize_digest(value: str) -> str:
    return value.lower() if value.startswith("sha256:") else f"sha256:{value.lower()}"


def _without_digest(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("manifest_digest", None)
    return result


def manifest_digest(value: Mapping[str, Any]) -> str:
    """Return the canonical content digest, excluding ``manifest_digest``."""

    return _digest_json(_without_digest(value))


def _unknown_fields(value: Mapping[str, Any], allowed: set[str], field: str, errors: list[str]) -> None:
    for key in sorted(set(value) - allowed):
        errors.append(f"{field} has unknown field {key}")


def _required_string(value: object, field: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a non-empty string")
        return False
    return True


def _validate_digest(value: object, field: str, errors: list[str]) -> None:
    if not _valid_digest(value):
        errors.append(f"{field} must be a SHA-256 digest")


def _validate_executor(value: object, surface: object, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append("adapter manifest executor must be an object")
        return
    _unknown_fields(value, {"executor_id", "kind", "version", "digest", "trust_level"}, "adapter manifest executor", errors)
    if _required_string(value.get("executor_id"), "adapter manifest executor.executor_id", errors) and not ID_RE.fullmatch(str(value["executor_id"])):
        errors.append("adapter manifest executor.executor_id is invalid")
    if _required_string(value.get("version"), "adapter manifest executor.version", errors) and not VERSION_RE.fullmatch(str(value["version"])):
        errors.append("adapter manifest executor.version is invalid")
    _validate_digest(value.get("digest"), "adapter manifest executor.digest", errors)
    if value.get("kind") not in EXECUTOR_KINDS:
        errors.append("adapter manifest executor.kind is invalid")
    if value.get("trust_level") not in TRUST_LEVELS:
        errors.append("adapter manifest executor.trust_level is invalid")
    if isinstance(surface, str) and surface in SURFACES and isinstance(value.get("kind"), str) and value["kind"] not in ALLOWED_EXECUTOR_KINDS[surface]:
        errors.append(f"adapter manifest executor.kind is not allowed for surface={surface}")


def _validate_token_list(value: object, field: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{field} must be an array")
        return
    if not value:
        errors.append(f"{field} must contain at least one entry")
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{field}[{index}] must be a non-empty string")
            continue
        if TOKEN_RE.fullmatch(item) is None:
            errors.append(f"{field}[{index}] is invalid")
        if item in seen:
            errors.append(f"{field}[{index}] is duplicated")
        seen.add(item)
    if all(isinstance(item, str) for item in value) and sorted(value) != value:
        errors.append(f"{field} must be sorted")


def _validate_authorization(value: object, surface: object, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append("adapter manifest authorization must be an object")
        return
    _unknown_fields(value, {"allowed_projects", "allowed_partitions"}, "adapter manifest authorization", errors)
    _validate_token_list(value.get("allowed_projects"), "adapter manifest authorization.allowed_projects", errors)
    if "allowed_partitions" in value:
        _validate_token_list(value.get("allowed_partitions"), "adapter manifest authorization.allowed_partitions", errors)
    elif surface == "vc":
        errors.append("adapter manifest authorization.allowed_partitions must be an array")


def _validate_positive_bound(value: object, field: str, maximum: int, errors: list[str]) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        errors.append(f"{field} must be a positive integer")
    elif value > maximum:
        errors.append(f"{field} exceeds the maximum allowed value")


def _validate_resources(value: object, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append("adapter manifest resource_limits must be an object")
        return
    _unknown_fields(value, {"max_gpus", "max_memory_gb", "max_cpus"}, "adapter manifest resource_limits", errors)
    for field in ("max_gpus", "max_memory_gb", "max_cpus"):
        _validate_positive_bound(value.get(field), f"adapter manifest resource_limits.{field}", MAX_RESOURCE, errors)


def _validate_timeouts(value: object, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append("adapter manifest timeouts must be an object")
        return
    fields = ("submit_seconds", "wait_seconds", "command_seconds", "cancel_seconds", "poll_seconds")
    _unknown_fields(value, set(fields), "adapter manifest timeouts", errors)
    for field in fields:
        _validate_positive_bound(value.get(field), f"adapter manifest timeouts.{field}", MAX_TIMEOUT_SECONDS, errors)
    command = value.get("command_seconds")
    wait = value.get("wait_seconds")
    poll = value.get("poll_seconds")
    if isinstance(command, int) and not isinstance(command, bool) and isinstance(wait, int) and not isinstance(wait, bool) and command > wait:
        errors.append("adapter manifest timeouts.command_seconds must not exceed wait_seconds")
    if isinstance(poll, int) and not isinstance(poll, bool) and isinstance(wait, int) and not isinstance(wait, bool) and poll > wait:
        errors.append("adapter manifest timeouts.poll_seconds must not exceed wait_seconds")


def _validate_cancellation(value: object, surface: object, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append("adapter manifest cancellation must be an object")
        return
    _unknown_fields(value, {"supported", "strategy", "confirmation", "timeout_outcome"}, "adapter manifest cancellation", errors)
    if not isinstance(value.get("supported"), bool):
        errors.append("adapter manifest cancellation.supported must be boolean")
    if value.get("strategy") not in CANCELLATION_STRATEGIES:
        errors.append("adapter manifest cancellation.strategy is invalid")
    if value.get("confirmation") not in CANCELLATION_CONFIRMATIONS:
        errors.append("adapter manifest cancellation.confirmation is invalid")
    if value.get("timeout_outcome") not in TIMEOUT_OUTCOMES:
        errors.append("adapter manifest cancellation.timeout_outcome is invalid")
    if value.get("supported") is False:
        if value.get("strategy") != "none":
            errors.append("unsupported cancellation must use strategy=none")
        if value.get("confirmation") != "not_applicable":
            errors.append("unsupported cancellation must use confirmation=not_applicable")
        if value.get("timeout_outcome") != "BLOCKED":
            errors.append("unsupported cancellation must use timeout_outcome=BLOCKED")
    if value.get("supported") is True and value.get("strategy") == "none":
        errors.append("supported cancellation cannot use strategy=none")
    if value.get("supported") is True and value.get("confirmation") == "not_applicable":
        errors.append("supported cancellation cannot use confirmation=not_applicable")
    if value.get("confirmation") == "confirmed" and value.get("timeout_outcome") != "CANCELLED":
        errors.append("confirmed cancellation must use timeout_outcome=CANCELLED")
    if value.get("confirmation") == "best_effort" and value.get("timeout_outcome") != "BLOCKED":
        errors.append("best_effort cancellation must use timeout_outcome=BLOCKED")
    if surface == "vc" and value.get("supported") is not True:
        errors.append("vc adapter manifest requires supported cancellation")


def _validate_output_scope(value: object, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append("adapter manifest output_scope must be an object")
        return
    _unknown_fields(value, {"output_root", "logs_root", "write_policy", "logs_retained"}, "adapter manifest output_scope", errors)
    for field in ("output_root", "logs_root"):
        path = value.get(field)
        if not isinstance(path, str) or RELATIVE_PATH_RE.fullmatch(path) is None:
            errors.append(f"adapter manifest output_scope.{field} must be a normalized non-escaping relative POSIX path")
    output_root = value.get("output_root")
    logs_root = value.get("logs_root")
    if isinstance(output_root, str) and isinstance(logs_root, str) and logs_root != output_root and not logs_root.startswith(f"{output_root}/"):
        errors.append("adapter manifest output_scope.logs_root must be inside output_root")
    if value.get("write_policy") not in WRITE_POLICIES:
        errors.append("adapter manifest output_scope.write_policy is invalid")
    if not isinstance(value.get("logs_retained"), bool):
        errors.append("adapter manifest output_scope.logs_retained must be boolean")


def _validate_runtime(value: object, surface: object, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append("adapter manifest runtime must be an object")
        return
    _unknown_fields(value, {"runtime_identity_digest", "container"}, "adapter manifest runtime", errors)
    _validate_digest(value.get("runtime_identity_digest"), "adapter manifest runtime.runtime_identity_digest", errors)
    container = value.get("container")
    if "container" in value:
        if not isinstance(container, Mapping):
            errors.append("adapter manifest runtime.container must be an object")
        else:
            _unknown_fields(container, {"image", "image_digest"}, "adapter manifest runtime.container", errors)
            if _required_string(container.get("image"), "adapter manifest runtime.container.image", errors):
                match = re.search(r"@sha256:([0-9a-f]{64})$", str(container["image"]), re.IGNORECASE)
                if match is None:
                    errors.append("adapter manifest runtime.container.image must be digest-pinned")
                elif not _valid_digest(container.get("image_digest")) or not _same_digest(str(container["image_digest"]), f"sha256:{match.group(1)}"):
                    errors.append("adapter manifest runtime.container.image_digest does not match image")
            _validate_digest(container.get("image_digest"), "adapter manifest runtime.container.image_digest", errors)
    if surface == "vc" and container is None:
        errors.append("vc adapter manifest requires runtime.container")


def _validate_attestation(value: object, surface: object, executor: object, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append("adapter manifest attestation must be an object")
        return
    _unknown_fields(value, {"mode"}, "adapter manifest attestation", errors)
    if value.get("mode") not in ATTESTATION_MODES:
        errors.append("adapter manifest attestation.mode is invalid")
    trust = executor.get("trust_level") if isinstance(executor, Mapping) else None
    if trust == "attested" and value.get("mode") == "none":
        errors.append("attested executor requires a non-none attestation mode")
    if value.get("mode") == "trusted_attestation" and trust != "attested":
        errors.append("trusted_attestation requires an attested executor")
    if surface == "trusted" and trust != "attested":
        errors.append("trusted surface requires an attested executor")
    if surface == "trusted" and value.get("mode") not in {"trusted_attestation", "signed_receipt"}:
        errors.append("trusted surface requires signed_receipt or trusted_attestation")


def validate_adapter_manifest(value: object) -> list[str]:
    """Return deterministic structural and semantic errors for a manifest."""

    if not isinstance(value, Mapping):
        return ["adapter manifest must be an object"]
    errors: list[str] = []
    allowed = {
        "schema", "manifest_id", "manifest_version", "surface", "executor", "policy_snapshot_digest",
        "authorization", "resource_limits", "timeouts", "cancellation", "output_scope", "runtime",
        "attestation", "manifest_digest",
    }
    _unknown_fields(value, allowed, "adapter manifest", errors)
    if value.get("schema") != MANIFEST_SCHEMA:
        errors.append("adapter manifest schema is unsupported")
    if _required_string(value.get("manifest_id"), "adapter manifest manifest_id", errors) and not ID_RE.fullmatch(str(value["manifest_id"])):
        errors.append("adapter manifest manifest_id is invalid")
    if _required_string(value.get("manifest_version"), "adapter manifest manifest_version", errors) and not VERSION_RE.fullmatch(str(value["manifest_version"])):
        errors.append("adapter manifest manifest_version is invalid")
    if value.get("surface") not in SURFACES:
        errors.append("adapter manifest surface is invalid")
    _validate_digest(value.get("policy_snapshot_digest"), "adapter manifest policy_snapshot_digest", errors)
    _validate_executor(value.get("executor"), value.get("surface"), errors)
    _validate_authorization(value.get("authorization"), value.get("surface"), errors)
    _validate_resources(value.get("resource_limits"), errors)
    _validate_timeouts(value.get("timeouts"), errors)
    _validate_cancellation(value.get("cancellation"), value.get("surface"), errors)
    _validate_output_scope(value.get("output_scope"), errors)
    _validate_runtime(value.get("runtime"), value.get("surface"), errors)
    _validate_attestation(value.get("attestation"), value.get("surface"), value.get("executor"), errors)
    _validate_digest(value.get("manifest_digest"), "adapter manifest manifest_digest", errors)
    if not errors:
        expected = manifest_digest(value)
        if not _same_digest(str(value["manifest_digest"]), expected):
            errors.append("adapter manifest manifest_digest does not match its contents")
    return errors


def create_adapter_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize allowlists/digests and add a content self-binding."""

    result = dict(value)
    result["schema"] = MANIFEST_SCHEMA
    result["executor"] = {**dict(value["executor"]), "digest": _normalize_digest(str(value["executor"]["digest"]))}
    result["policy_snapshot_digest"] = _normalize_digest(str(value["policy_snapshot_digest"]))
    result["authorization"] = {
        "allowed_projects": sorted(value["authorization"]["allowed_projects"]),
        **(
            {}
            if value["authorization"].get("allowed_partitions") is None
            else {"allowed_partitions": sorted(value["authorization"]["allowed_partitions"])}
        ),
    }
    runtime = dict(value["runtime"])
    runtime["runtime_identity_digest"] = _normalize_digest(str(runtime["runtime_identity_digest"]))
    if isinstance(runtime.get("container"), Mapping):
        runtime["container"] = {
            **dict(runtime["container"]),
            "image_digest": _normalize_digest(str(runtime["container"]["image_digest"])),
        }
    result["runtime"] = runtime
    result["manifest_digest"] = ""
    result["manifest_digest"] = manifest_digest(result)
    return result


def admit_adapter_manifest(value: object, context: Mapping[str, Any]) -> list[str]:
    """Apply a run-specific policy/surface/resource binding after validation."""

    if not isinstance(context, Mapping):
        return ["adapter admission context must be an object"]
    errors = validate_adapter_manifest(value)
    if errors:
        return errors
    assert isinstance(value, Mapping)
    expected_policy = context.get("policy_snapshot_digest")
    if not _valid_digest(expected_policy):
        errors.append("adapter admission policy_snapshot_digest must be a SHA-256 digest")
    elif not _same_digest(str(value["policy_snapshot_digest"]), str(expected_policy)):
        errors.append("adapter manifest policy_snapshot_digest does not match admission context")
    if value["surface"] != context.get("surface"):
        errors.append("adapter manifest surface does not match admission context")
    policy_surfaces = context.get("policy_surfaces")
    if not isinstance(policy_surfaces, list):
        errors.append("adapter admission policy_surfaces must be an array")
    else:
        if any(surface not in SURFACES for surface in policy_surfaces):
            errors.append("adapter admission policy_surfaces contains an invalid surface")
        if value["surface"] not in policy_surfaces:
            errors.append("adapter manifest surface is not enabled by policy")
    context_executor = context.get("executor")
    if not isinstance(context_executor, Mapping):
        errors.append("adapter admission executor must be an object")
    else:
        manifest_executor = value["executor"]
        for field in ("executor_id", "kind", "version", "trust_level"):
            if context_executor.get(field) != manifest_executor[field]:
                errors.append(f"adapter manifest executor.{field} does not match admission context")
        if not _valid_digest(context_executor.get("digest")) or not _same_digest(str(context_executor.get("digest")), manifest_executor["digest"]):
            errors.append("adapter manifest executor.digest does not match admission context")
    runtime_identity = context.get("runtime_identity_digest")
    if not _valid_digest(runtime_identity):
        errors.append("adapter admission runtime_identity_digest must be a SHA-256 digest")
    elif not _same_digest(value["runtime"]["runtime_identity_digest"], str(runtime_identity)):
        errors.append("adapter manifest runtime_identity_digest does not match admission context")
    if isinstance(value["runtime"].get("container"), Mapping):
        observed_container_digest = context.get("container_image_digest")
        if not _valid_digest(observed_container_digest):
            errors.append("adapter admission container_image_digest must be a SHA-256 digest")
        elif not _same_digest(value["runtime"]["container"]["image_digest"], str(observed_container_digest)):
            errors.append("adapter manifest container image digest does not match admission context")
    output_scope = context.get("output_scope")
    if not isinstance(output_scope, Mapping):
        errors.append("adapter admission output_scope must be an object")
    else:
        if output_scope.get("output_root") != value["output_scope"]["output_root"]:
            errors.append("adapter manifest output_root does not match admission context")
        if output_scope.get("logs_root") != value["output_scope"]["logs_root"]:
            errors.append("adapter manifest logs_root does not match admission context")
    policy_authorization = context.get("policy_authorization")
    if not isinstance(policy_authorization, Mapping):
        errors.append("adapter admission policy_authorization must be an object")
    else:
        policy_errors: list[str] = []
        _validate_token_list(
            policy_authorization.get("allowed_projects"),
            "adapter admission policy_authorization.allowed_projects",
            policy_errors,
        )
        if "allowed_partitions" in policy_authorization:
            _validate_token_list(
                policy_authorization.get("allowed_partitions"),
                "adapter admission policy_authorization.allowed_partitions",
                policy_errors,
            )
        elif value["surface"] == "vc":
            policy_errors.append("adapter admission policy_authorization.allowed_partitions must be an array")
        errors.extend(policy_errors)
        if not policy_errors:
            if any(project not in policy_authorization["allowed_projects"] for project in value["authorization"]["allowed_projects"]):
                errors.append("adapter manifest project allowlist exceeds policy authorization")
            manifest_partitions = value["authorization"].get("allowed_partitions")
            if manifest_partitions is not None and (
                policy_authorization.get("allowed_partitions") is None
                or any(partition not in policy_authorization["allowed_partitions"] for partition in manifest_partitions)
            ):
                errors.append("adapter manifest partition allowlist exceeds policy authorization")
    if value["surface"] == "vc":
        project = context.get("project")
        partition = context.get("partition")
        if not isinstance(project, str) or not project:
            errors.append("vc adapter admission requires project")
        elif project not in value["authorization"]["allowed_projects"]:
            errors.append("project is not allowed by adapter manifest")
        if not isinstance(partition, str) or not partition:
            errors.append("vc adapter admission requires partition")
        elif partition not in (value["authorization"].get("allowed_partitions") or []):
            errors.append("partition is not allowed by adapter manifest")
    resources = context.get("resources")
    if isinstance(resources, Mapping):
        limits = value["resource_limits"]
        for field, limit_field in (("gpus", "max_gpus"), ("memory_gb", "max_memory_gb"), ("cpus", "max_cpus")):
            requested = resources.get(field)
            if requested is None:
                continue
            if not isinstance(requested, int) or isinstance(requested, bool) or requested <= 0:
                errors.append(f"admission resources.{field} must be a positive integer")
            elif requested > limits[limit_field]:
                errors.append(f"admission resources.{field} exceeds adapter manifest limit")
    timeouts = context.get("timeouts")
    if isinstance(timeouts, Mapping):
        limits = value["timeouts"]
        for field in ("submit_seconds", "wait_seconds", "command_seconds", "cancel_seconds", "poll_seconds"):
            requested = timeouts.get(field)
            if requested is None:
                continue
            if not isinstance(requested, int) or isinstance(requested, bool) or requested <= 0:
                errors.append(f"admission timeouts.{field} must be a positive integer")
            elif requested > limits[field]:
                errors.append(f"admission timeouts.{field} exceeds adapter manifest limit")
    return errors
