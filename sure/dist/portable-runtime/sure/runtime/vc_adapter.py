"""Opt-in, host-neutral VC adapter port for the SURE execution contract.

This module is intentionally a port, not a second workflow engine.  It binds a
validated site-policy snapshot and adapter manifest to an execution request,
persists that request before invoking a runner, and projects the runner result
through :mod:`sure.runtime.execution_bridge`.  It never advances a workflow.

The default path is cooperative and local-staging-only.  A real VC submitter
must be explicitly supplied (or explicitly enabled through ``allow_submit``),
which keeps portable skills useful for dry runs and tests without silently
turning a missing executor into a successful execution.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from sure.site.loader import validate_site_policy

from .adapter_manifest import admit_adapter_manifest, validate_adapter_manifest
from .execution_bridge import (
    artifact_ref,
    build_request,
    create_execution_admission_trace,
    derive_execution_admission_trace,
    digest_json,
    map_vc_job_result_to_receipt,
    capability_summary,
    same_digest,
    safe_id,
    validate_capability_evidence_list,
    validate_output_contract,
    write_contract_bundle,
    write_json,
    utc_now,
    valid_digest,
)


VC_ADAPTER_PORT_SCHEMA = "sure.execution.vc_adapter_port.v1"
_POLICY_SNAPSHOT_SCHEMA = "sure.policy.snapshot.v1"
_POLICY_PATH_ROLES = {
    "read_only_reference",
    "controlled_publication",
    "dataset_source",
    "runtime_cache",
    "forbidden_output",
}


class VcAdapterError(ValueError):
    """A request cannot be admitted to the opt-in VC adapter port."""


@dataclass(frozen=True)
class VcAdapterPreparation:
    """The immutable bindings used to build and execute one VC request."""

    request: dict[str, Any]
    manifest: dict[str, Any]
    admission_context: dict[str, Any]
    policy_projection: dict[str, Any]


@dataclass(frozen=True)
class VcAdapterRun:
    """Artifacts emitted by :func:`run_vc_adapter`.

    ``receipt`` and ``contract`` are optional only for a rejected preflight.
    A capability-missing result is represented by a real ``NOT_STARTED``
    receipt, preserving the distinction between absence and rejection.
    """

    request: dict[str, Any]
    admission: dict[str, Any]
    receipt: dict[str, Any] | None
    contract: dict[str, Any] | None
    submitted: bool


Submitter = Callable[[Mapping[str, Any]], object]


def _object(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VcAdapterError(f"{field} must be an object")
    return value


def _normalized_digest(value: object, field: str) -> str:
    if not valid_digest(value):
        raise VcAdapterError(f"{field} must be a SHA-256 digest")
    text = str(value).lower()
    return text if text.startswith("sha256:") else f"sha256:{text}"


def _same_or_error(left: object, right: object, field: str) -> None:
    if not same_digest(left, right):
        raise VcAdapterError(f"{field} does not match its bound digest")


def _normalized_absolute(path: object, field: str) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise VcAdapterError(f"{field} must be an absolute path")
    normalized = os.path.normpath(path)
    if normalized != path:
        raise VcAdapterError(f"{field} must be normalized")
    return normalized


def _verify_policy_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute the policy snapshot digests before it can authorize a port."""

    allowed = {
        "schema",
        "site_id",
        "policy_version",
        "policy",
        "source",
        "path_bindings",
        "policy_digest",
        "bindings_digest",
        "snapshot_digest",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise VcAdapterError(f"policy snapshot has unknown field: {unknown[0]}")
    if value.get("schema") != _POLICY_SNAPSHOT_SCHEMA:
        raise VcAdapterError(f"policy snapshot schema must be {_POLICY_SNAPSHOT_SCHEMA}")

    policy = validate_site_policy(value.get("policy"))
    if value.get("policy") != policy:
        raise VcAdapterError("policy snapshot policy is not canonically normalized")
    if value.get("site_id") != policy["site_id"] or value.get("policy_version") != policy["policy_version"]:
        raise VcAdapterError("policy snapshot identity does not match its policy")

    source = _object(value.get("source"), "policy snapshot source")
    source_unknown = sorted(set(source) - {"kind", "path", "raw_sha256"})
    if source_unknown:
        raise VcAdapterError(f"policy snapshot source has unknown field: {source_unknown[0]}")
    source_kind = source.get("kind")
    if not isinstance(source_kind, str) or not source_kind:
        raise VcAdapterError("policy snapshot source.kind must be a non-empty string")
    source_digest = _normalized_digest(source.get("raw_sha256"), "policy snapshot source.raw_sha256")
    normalized_source: dict[str, Any] = {"kind": source_kind, "raw_sha256": source_digest}
    if "path" in source:
        normalized_source["path"] = _normalized_absolute(source.get("path"), "policy snapshot source.path")

    raw_bindings = value.get("path_bindings")
    if not isinstance(raw_bindings, list):
        raise VcAdapterError("policy snapshot path_bindings must be a list")
    bindings: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_bindings):
        binding = _object(raw, f"policy snapshot path_bindings[{index}]")
        unknown_binding = sorted(set(binding) - {"root_id", "role", "path", "resolved_path"})
        if unknown_binding:
            raise VcAdapterError(f"policy snapshot path_bindings[{index}] has unknown field: {unknown_binding[0]}")
        root_id = binding.get("root_id")
        role = binding.get("role")
        if not isinstance(root_id, str) or not root_id or not isinstance(role, str) or role not in _POLICY_PATH_ROLES:
            raise VcAdapterError(f"policy snapshot path_bindings[{index}] identity is invalid")
        if root_id in seen_ids:
            raise VcAdapterError(f"policy snapshot path_bindings contains duplicate root_id: {root_id}")
        seen_ids.add(root_id)
        bindings.append(
            {
                "root_id": root_id,
                "role": role,
                "path": _normalized_absolute(binding.get("path"), f"policy snapshot path_bindings[{index}].path"),
                "resolved_path": _normalized_absolute(
                    binding.get("resolved_path"), f"policy snapshot path_bindings[{index}].resolved_path"
                ),
            }
        )
    bindings.sort(key=lambda item: item["root_id"])

    policy_digest = digest_json(policy)
    bindings_digest = digest_json(bindings)
    payload = {
        "schema": _POLICY_SNAPSHOT_SCHEMA,
        "site_id": policy["site_id"],
        "policy_version": policy["policy_version"],
        "policy": policy,
        "source": normalized_source,
        "path_bindings": bindings,
        "policy_digest": policy_digest,
        "bindings_digest": bindings_digest,
    }
    snapshot_digest = digest_json(payload)
    _same_or_error(value.get("policy_digest"), policy_digest, "policy snapshot policy_digest")
    _same_or_error(value.get("bindings_digest"), bindings_digest, "policy snapshot bindings_digest")
    _same_or_error(value.get("snapshot_digest"), snapshot_digest, "policy snapshot snapshot_digest")
    return {
        "schema": _POLICY_SNAPSHOT_SCHEMA,
        "site_id": policy["site_id"],
        "policy_version": policy["policy_version"],
        "policy": policy,
        "source": normalized_source,
        "path_bindings": bindings,
        "policy_digest": policy_digest,
        "bindings_digest": bindings_digest,
        "snapshot_digest": snapshot_digest,
    }


def verify_policy_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    """Public, in-memory policy snapshot verification for adapter callers."""

    return _verify_policy_snapshot(_object(value, "policy snapshot"))


def project_vc_policy_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project site policy v1 into the VC authorization understood by Core."""

    snapshot = verify_policy_snapshot(value)
    policy = _object(snapshot["policy"], "policy snapshot policy")
    execution = _object(policy.get("execution"), "policy.execution")
    surfaces = execution.get("surfaces")
    if not isinstance(surfaces, list) or "vc" not in surfaces:
        raise VcAdapterError("site policy does not enable execution surface vc")
    project = execution.get("vc_project")
    partitions = execution.get("vc_partitions")
    if not isinstance(project, str) or not project:
        raise VcAdapterError("site policy vc execution requires execution.vc_project")
    if not isinstance(partitions, list) or not partitions:
        raise VcAdapterError("site policy vc execution requires non-empty execution.vc_partitions")

    bindings = snapshot["path_bindings"]
    if not isinstance(bindings, list):
        raise VcAdapterError("policy snapshot path_bindings must be a list")
    storage = _object(policy.get("storage"), "policy.storage")
    allowed_paths = [*storage.get("approved_results_roots", []), storage.get("runtime_root")]
    allowed_roots: list[dict[str, str]] = []
    forbidden_roots: list[dict[str, str]] = []
    for path in allowed_paths:
        matches = [
            item
            for item in bindings
            if isinstance(item, Mapping)
            and item.get("path") == path
            and item.get("role") in {"controlled_publication", "runtime_cache"}
        ]
        if len(matches) != 1:
            raise VcAdapterError(f"policy snapshot requires exactly one writable binding for {path}")
        allowed_roots.append({"path": str(matches[0]["path"]), "resolved_path": str(matches[0]["resolved_path"])})
    for path in storage.get("forbidden_output_roots", []):
        matches = [
            item
            for item in bindings
            if isinstance(item, Mapping) and item.get("path") == path and item.get("role") == "forbidden_output"
        ]
        if len(matches) != 1:
            raise VcAdapterError(f"policy snapshot requires exactly one forbidden binding for {path}")
        forbidden_roots.append({"path": str(matches[0]["path"]), "resolved_path": str(matches[0]["resolved_path"])})

    return {
        "policy_snapshot": snapshot,
        "policy_snapshot_digest": snapshot["snapshot_digest"],
        "policy_digest": snapshot["policy_digest"],
        "policy_surfaces": ["vc"],
        "policy_authorization": {
            "allowed_projects": [project],
            "allowed_partitions": sorted(str(item) for item in partitions),
        },
        "allowed_output_roots": allowed_roots,
        "forbidden_output_roots": forbidden_roots,
    }


def _path_under(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _assert_staging_root(staging_root: Path, projection: Mapping[str, Any]) -> Path:
    root = staging_root.expanduser().absolute()
    if root.exists() and root.is_symlink():
        raise VcAdapterError("staging_root must not be a symlink")
    allowed = projection.get("allowed_output_roots")
    forbidden = projection.get("forbidden_output_roots")
    if not isinstance(allowed, list) or not any(
        isinstance(item, Mapping) and _path_under(root, Path(str(item.get("resolved_path") or ""))) for item in allowed
    ):
        raise VcAdapterError("staging_root is outside site-policy writable roots")
    if isinstance(forbidden, list) and any(
        isinstance(item, Mapping) and _path_under(root, Path(str(item.get("resolved_path") or ""))) for item in forbidden
    ):
        raise VcAdapterError("staging_root is inside a forbidden output root")
    return root


def _manifest_executor(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    executor = _object(manifest.get("executor"), "adapter manifest executor")
    for key in ("executor_id", "kind", "version", "digest", "trust_level"):
        if key not in executor:
            raise VcAdapterError(f"adapter manifest executor.{key} is required")
    return executor


def build_vc_admission_context(
    policy_snapshot: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    executor: Mapping[str, Any],
    runtime_identity_digest: str,
    container_image_digest: str,
    output_scope: Mapping[str, Any],
    project: str,
    partition: str,
    resources: Mapping[str, Any] | None = None,
    timeouts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify the manifest and bind it to independently observed host values."""

    manifest_errors = validate_adapter_manifest(manifest)
    if manifest_errors:
        raise VcAdapterError("adapter manifest rejected: " + "; ".join(manifest_errors))
    if manifest.get("surface") != "vc":
        raise VcAdapterError("VC adapter port requires a vc manifest")
    if executor.get("kind") == "trusted" or executor.get("trust_level") == "attested":
        raise VcAdapterError("trusted/attested VC execution remains deferred; use the trusted port when attestation exists")
    projection = project_vc_policy_snapshot(policy_snapshot)
    context: dict[str, Any] = {
        "policy_snapshot_digest": projection["policy_snapshot_digest"],
        "surface": "vc",
        "policy_surfaces": projection["policy_surfaces"],
        "executor": dict(executor),
        "policy_authorization": projection["policy_authorization"],
        "runtime_identity_digest": _normalized_digest(runtime_identity_digest, "runtime_identity_digest"),
        "container_image_digest": _normalized_digest(container_image_digest, "container_image_digest"),
        "output_scope": {
            "output_root": str(output_scope.get("output_root") or ""),
            "logs_root": str(output_scope.get("logs_root") or ""),
        },
        "project": project,
        "partition": partition,
    }
    if resources is not None:
        context["resources"] = dict(resources)
    if timeouts is not None:
        context["timeouts"] = dict(timeouts)
    errors = admit_adapter_manifest(manifest, context)
    if errors:
        raise VcAdapterError("adapter admission rejected: " + "; ".join(errors))
    return context


def build_vc_request(
    *,
    policy_projection: Mapping[str, Any],
    admission_context: Mapping[str, Any],
    manifest: Mapping[str, Any],
    run_id: str,
    unit_id: str,
    operation: str,
    staging_root: Path,
    entrypoint: Mapping[str, Any],
    subject: Mapping[str, Any],
    capability_requirements: Sequence[Mapping[str, Any]],
    inputs: Sequence[Mapping[str, Any]] = (),
    output_contract: Mapping[str, Any] | None = None,
    attempt: int = 1,
    request_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create an external request only after policy/manifest admission."""

    root = _assert_staging_root(staging_root, policy_projection)
    if output_contract is not None:
        errors = validate_output_contract(output_contract)
        if errors:
            raise VcAdapterError("output contract rejected: " + "; ".join(errors))
    executor = _manifest_executor(manifest)
    if admission_context.get("executor") != dict(executor):
        raise VcAdapterError("admission context executor does not match manifest")
    runtime_requirements: dict[str, Any] = {
        "execution_surface": "vc",
        "executor_kind": str(executor["kind"]),
        "vc_project": str(admission_context["project"]),
        "vc_partition": str(admission_context["partition"]),
    }
    resources = admission_context.get("resources")
    if isinstance(resources, Mapping):
        for source, target in (("gpus", "vc_gpus"), ("memory_gb", "vc_memory_gb"), ("cpus", "vc_cpus")):
            if source in resources:
                runtime_requirements[target] = resources[source]
    timeouts = admission_context.get("timeouts")
    if isinstance(timeouts, Mapping):
        runtime_requirements["adapter_timeouts"] = dict(timeouts)
    request = build_request(
        run_id=run_id,
        unit_id=unit_id,
        operation=operation,
        entrypoint=entrypoint,
        output_root=root,
        subject=subject,
        inputs=inputs,
        capability_requirements=capability_requirements,
        runtime_requirements=runtime_requirements,
        attempt=attempt,
        policy_digest=str(policy_projection["policy_digest"]),
        policy_snapshot_digest=str(policy_projection["policy_snapshot_digest"]),
        adapter_manifest_digest=str(manifest["manifest_digest"]),
        request_id=request_id,
        created_at=created_at,
        output_contract=output_contract,
    )
    _same_or_error(request.get("policy_snapshot_digest"), policy_projection["policy_snapshot_digest"], "request policy snapshot")
    _same_or_error(request.get("adapter_manifest_digest"), manifest.get("manifest_digest"), "request adapter manifest")
    return request


def normalize_vc_job_result(result: object) -> dict[str, Any]:
    """Convert a legacy ``VcJobResult`` or JSON result into a plain mapping."""

    if isinstance(result, Mapping):
        value = dict(result)
    elif dataclasses.is_dataclass(result):
        value = dataclasses.asdict(result)
    elif hasattr(result, "__dict__"):
        value = dict(vars(result))
    else:
        raise VcAdapterError("VC runner result must be a mapping or dataclass")
    for key, raw in list(value.items()):
        if isinstance(raw, Path):
            value[key] = str(raw)
    return value


def _declared_outputs(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    contract = request.get("output_contract")
    if not isinstance(contract, Mapping):
        return []
    root_value = _object(request.get("output_root"), "request.output_root")
    root = Path(str(root_value.get("resolved_path") or "")).expanduser().resolve()
    outputs: list[dict[str, Any]] = []
    for raw in contract.get("outputs", []):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("path"), str):
            continue
        candidate = root / PurePosixPath(str(raw["path"]))
        if not candidate.exists():
            continue
        outputs.append(
            artifact_ref(
                candidate,
                origin="generated",
                source_root=root,
                artifact_id=str(raw.get("artifact_id") or safe_id(raw.get("path"), "output")),
            )
        )
    return outputs


def _runner_payload(request: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(str(_object(request.get("output_root"), "request.output_root").get("resolved_path") or "")).resolve()
    runtime = _object(request.get("runtime_requirements"), "request.runtime_requirements")
    entry = _object(request.get("entrypoint"), "request.entrypoint")
    executable = str(entry.get("executable") or "")
    argv = entry.get("argv") if isinstance(entry.get("argv"), list) else []
    if not executable:
        raise VcAdapterError("request entrypoint executable is required")
    container = _object(_object(manifest.get("runtime"), "adapter manifest runtime").get("container"), "adapter manifest runtime.container")
    logs_root = str(_object(manifest.get("output_scope"), "adapter manifest output_scope").get("logs_root") or "")
    log_dir = root / PurePosixPath(logs_root)
    if not _path_under(log_dir, root):
        raise VcAdapterError("adapter manifest logs_root escapes staging_root")
    return {
        "image": str(container["image"]),
        "command": shlex.join([executable, *[str(item) for item in argv]]),
        "log_dir": log_dir,
        "partition": str(runtime["vc_partition"]),
        "project": str(runtime["vc_project"]),
        "gpus": int(runtime.get("vc_gpus") or 1),
        "memory_gb": int(runtime.get("vc_memory_gb") or 1),
        "cpus": int(runtime.get("vc_cpus") or 1),
        "timeout_seconds": int(_object(runtime.get("adapter_timeouts"), "runtime.adapter_timeouts").get("wait_seconds") or 1)
        if isinstance(runtime.get("adapter_timeouts"), Mapping)
        else 1,
        "command_timeout_seconds": int(_object(runtime.get("adapter_timeouts"), "runtime.adapter_timeouts").get("command_seconds") or 1)
        if isinstance(runtime.get("adapter_timeouts"), Mapping)
        else 1,
        "poll_interval": int(_object(runtime.get("adapter_timeouts"), "runtime.adapter_timeouts").get("poll_seconds") or 1)
        if isinstance(runtime.get("adapter_timeouts"), Mapping)
        else 1,
        "request_id": request.get("request_id"),
    }


def _default_submitter(payload: Mapping[str, Any]) -> object:
    """Bind the legacy runner only when an explicit port execution is requested."""

    if os.environ.get("SURE_ALLOW_REAL_VC_SUBMIT") != "1":
        raise VcAdapterError(
            "real VC submission is disabled by default; set SURE_ALLOW_REAL_VC_SUBMIT=1 "
            "or supply an injected submitter"
        )
    try:
        from vc_exec import run_vc_job
    except ImportError as error:  # pragma: no cover - exercised by portable hosts
        raise VcAdapterError("legacy vc_exec runner is not available; supply an explicit submitter") from error
    return run_vc_job(
        image=str(payload["image"]),
        command=str(payload["command"]),
        log_dir=Path(payload["log_dir"]),
        partition=str(payload["partition"]),
        project=str(payload["project"]),
        gpus=int(payload["gpus"]),
        memory_gb=int(payload["memory_gb"]),
        cpus=int(payload["cpus"]),
        timeout_seconds=float(payload["timeout_seconds"]),
        command_timeout_seconds=float(payload["command_timeout_seconds"]),
        poll_interval=float(payload["poll_interval"]),
    )


def run_vc_adapter(
    preparation: VcAdapterPreparation,
    *,
    staging_root: Path,
    submitter: Submitter | None = None,
    allow_submit: bool = False,
    capability_evidence_values: Sequence[Mapping[str, Any]] = (),
    capability_available: bool | None = None,
    cancellation_confirmed: bool | None = None,
    outputs: Sequence[Mapping[str, Any]] | None = None,
    residuals: Sequence[Mapping[str, Any]] = (),
    observed_at: str | None = None,
) -> VcAdapterRun:
    """Execute one explicitly admitted request and persist its contract bundle."""

    request = dict(preparation.request)
    manifest = dict(preparation.manifest)
    projection = preparation.policy_projection
    manifest_errors = validate_adapter_manifest(manifest)
    if manifest_errors:
        raise VcAdapterError("adapter manifest changed after preparation: " + "; ".join(manifest_errors))
    _same_or_error(request.get("adapter_manifest_digest"), manifest.get("manifest_digest"), "request adapter manifest")
    _same_or_error(request.get("policy_snapshot_digest"), projection.get("policy_snapshot_digest"), "request policy snapshot")
    runtime = _object(request.get("runtime_requirements"), "request.runtime_requirements")
    if runtime.get("execution_surface") != "vc":
        raise VcAdapterError("VC adapter request must declare execution_surface=vc")
    context_executor = preparation.admission_context.get("executor")
    if context_executor != manifest.get("executor"):
        raise VcAdapterError("admission context executor changed after preparation")
    root = _assert_staging_root(staging_root, projection)
    request_root = Path(str(_object(request.get("output_root"), "request.output_root").get("resolved_path") or "")).resolve()
    if request_root != root:
        raise VcAdapterError("request output root does not match adapter staging_root")
    evidence = list(capability_evidence_values)
    if validate_capability_evidence_list(evidence):
        raise VcAdapterError("invalid capability evidence supplied to VC adapter")
    requirements = request.get("capability_requirements")
    required_capabilities = requirements if isinstance(requirements, list) else []
    capability_summary_value = capability_summary(required_capabilities, evidence)

    # This is the ordering invariant: request bytes exist before a submitter
    # can be called.  A runner may inspect the file, but cannot replace it.
    root.mkdir(parents=True, exist_ok=True)
    request_path = root / "execution_request.json"
    write_json(request_path, request)
    if not same_digest(digest_json(json.loads(request_path.read_text(encoding="utf-8"))), digest_json(request)):
        raise VcAdapterError("persisted execution request does not round-trip")

    if capability_available is None:
        capability_available = bool(evidence) and bool(capability_summary_value["admitted"])
    elif capability_available and not bool(capability_summary_value["admitted"]):
        # A caller's boolean assertion cannot override missing/unknown
        # evidence for a required execution capability.
        capability_available = False
    submit_payload = _runner_payload(request, manifest)
    submitted = False
    runner_result: dict[str, Any]
    if not capability_available:
        runner_result = {"timed_out": False, "exit_code": None, "submitted": False}
    elif not allow_submit:
        admission = create_execution_admission_trace(
            request,
            observed_at=str(observed_at or utc_now()),
            outcome_reason_code="INVALID_CONTRACT",
            probe_invoked=False,
            execute_invoked=False,
        )
        write_json(root / "execution_admission.json", admission)
        return VcAdapterRun(request, admission, None, None, False)
    else:
        runner = submitter or _default_submitter
        try:
            runner_result = normalize_vc_job_result(runner(submit_payload))
            submitted = True
        except Exception as error:  # runner failures are a NOT_STARTED receipt, not a fake PASS
            runner_result = {
                "timed_out": False,
                "exit_code": None,
                "submitted": False,
                "vc_diagnostics": f"{error.__class__.__name__}: {error}",
            }
    if not isinstance(runner_result.get("timed_out"), bool):
        raise VcAdapterError("normalized VC result must contain boolean timed_out")
    if runner_result.get("submitted") is False:
        submitted = False
    result_capability = bool(capability_available)
    result_outputs = list(outputs) if outputs is not None else _declared_outputs(request)
    executor = _manifest_executor(manifest)
    receipt = map_vc_job_result_to_receipt(
        request,
        runner_result,
        executor_id=str(executor["executor_id"]),
        executor_version=str(executor["version"]),
        executor_digest_value=str(executor["digest"]),
        executor_trust_level=str(executor["trust_level"]),
        capability_evidence_values=evidence,
        capability_available=result_capability,
        submitted=submitted,
        cancellation_confirmed=cancellation_confirmed,
        outputs=result_outputs,
        residuals=list(residuals),
        observed_at=observed_at,
    )
    admission = derive_execution_admission_trace(
        request,
        receipt,
        probe_invoked=bool(evidence),
        execute_invoked=submitted,
        observed_at=observed_at,
        forbidden_output_roots=[Path(str(item["resolved_path"])) for item in projection.get("forbidden_output_roots", []) if isinstance(item, Mapping)],
    )
    contract = write_contract_bundle(
        root,
        request,
        receipt,
        admission_trace=admission,
        forbidden_output_roots=[Path(str(item["resolved_path"])) for item in projection.get("forbidden_output_roots", []) if isinstance(item, Mapping)],
    )
    return VcAdapterRun(request, admission, receipt, contract, submitted)


def prepare_vc_adapter(
    *,
    policy_snapshot: Mapping[str, Any],
    manifest: Mapping[str, Any],
    executor: Mapping[str, Any],
    runtime_identity_digest: str,
    container_image_digest: str,
    output_scope: Mapping[str, Any],
    project: str,
    partition: str,
    resources: Mapping[str, Any] | None,
    timeouts: Mapping[str, Any] | None,
    run_id: str,
    unit_id: str,
    operation: str,
    staging_root: Path,
    entrypoint: Mapping[str, Any],
    subject: Mapping[str, Any],
    capability_requirements: Sequence[Mapping[str, Any]],
    inputs: Sequence[Mapping[str, Any]] = (),
    output_contract: Mapping[str, Any] | None = None,
    attempt: int = 1,
    request_id: str | None = None,
    created_at: str | None = None,
) -> VcAdapterPreparation:
    projection = project_vc_policy_snapshot(policy_snapshot)
    context = build_vc_admission_context(
        policy_snapshot,
        manifest,
        executor=executor,
        runtime_identity_digest=runtime_identity_digest,
        container_image_digest=container_image_digest,
        output_scope=output_scope,
        project=project,
        partition=partition,
        resources=resources,
        timeouts=timeouts,
    )
    request = build_vc_request(
        policy_projection=projection,
        admission_context=context,
        manifest=manifest,
        run_id=run_id,
        unit_id=unit_id,
        operation=operation,
        staging_root=staging_root,
        entrypoint=entrypoint,
        subject=subject,
        capability_requirements=capability_requirements,
        inputs=inputs,
        output_contract=output_contract,
        attempt=attempt,
        request_id=request_id,
        created_at=created_at,
    )
    return VcAdapterPreparation(request, dict(manifest), context, projection)


__all__ = [
    "VC_ADAPTER_PORT_SCHEMA",
    "VcAdapterError",
    "VcAdapterPreparation",
    "VcAdapterRun",
    "build_vc_admission_context",
    "build_vc_request",
    "normalize_vc_job_result",
    "prepare_vc_adapter",
    "project_vc_policy_snapshot",
    "run_vc_adapter",
    "verify_policy_snapshot",
]
