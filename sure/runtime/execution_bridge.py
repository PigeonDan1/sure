"""Host-neutral execution request/receipt bridge for SURE Python runners.

The inference and transformation scripts predate the TypeScript SURE Core
executor.  They still have to emit their historical ``execution_surface`` and
``execution_result`` documents, but those documents are not an execution
authority.  This module gives the scripts one small, dependency-free contract
layer:

* a request is written before a process is launched;
* every launch outcome, including a preflight failure, gets a receipt; and
* the old JSON views are linked to the same request/receipt without changing
  their public schemas.

The bridge deliberately never reads or writes a workflow checkpoint.  A host
adapter may submit the receipt to ``surectl validate``; only SURE Core may
advance a unit.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import stat as stat_module
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REQUEST_SCHEMA = "sure.execution_request.v1"
RECEIPT_SCHEMA = "sure.execution_receipt.v1"
ADMISSION_SCHEMA = "sure.execution_admission.v1"
COMPATIBILITY_SCHEMA = "sure.execution_compatibility.v1"
OUTPUT_CONTRACT_SCHEMA = "sure.execution_output_contract.v1"
OUTPUT_SET_SCHEMA = "sure.execution.output-set.v1"
OUTPUT_MODES = {"preexisting", "mutating", "producing"}
OUTPUT_KINDS = {"file", "directory"}
OUTPUT_DIGEST_KINDS = {"file_sha256", "tree_sha256"}
CAPABILITY_CLASSES = {"agent_capability", "execution_capability"}
CAPABILITY_STATUSES = {"AVAILABLE", "MISSING", "UNKNOWN", "DENIED"}
CAPABILITY_SOURCES = {"agent", "host_probe", "executor", "site_policy", "trusted_attestation"}
EXECUTION_ADAPTER_SURFACES = {"vc", "remote", "trusted"}
EXECUTION_ADAPTER_KINDS = {
    "vc": {"remote", "trusted"},
    "remote": {"remote"},
    "trusted": {"trusted"},
}
MAX_ADAPTER_TIMEOUT_SECONDS = 604_800
DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CAPABILITY_ID_RE = re.compile(r"^sure\.[a-z0-9][a-z0-9.-]*$")
TERMINAL_LIFECYCLES = {"SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED"}
LIFECYCLES = {"NOT_STARTED", "QUEUED", "RUNNING", *TERMINAL_LIFECYCLES}
ADMISSION_STATUSES = {"ADMITTED", "CAPABILITY_MISSING", "REJECTED"}


def execution_outcome_projection(lifecycle: str) -> dict[str, Any]:
    """Mirror Core's lifecycle projection without advancing a workflow."""

    projections: dict[str, dict[str, Any]] = {
        "SUCCEEDED": {
            "validator_verdict": "NOT_EXECUTED",
            "workflow_disposition": "WAIT",
            "outcome": "NOT_EXECUTED",
            "reason_code": "VALIDATION_PENDING",
        },
        "FAILED": {
            "validator_verdict": "FAIL",
            "workflow_disposition": "RETRY",
            "outcome": "RETRY",
            "reason_code": "EXECUTION_FAILED",
        },
        "PARTIAL": {
            "validator_verdict": "FAIL",
            "workflow_disposition": "BLOCK",
            "outcome": "BLOCKED",
            "reason_code": "EXECUTION_PARTIAL",
        },
        "CANCELLED": {
            "validator_verdict": "NOT_EXECUTED",
            "workflow_disposition": "BLOCK",
            "outcome": "NOT_EXECUTED",
            "reason_code": "EXECUTION_CANCELLED",
        },
        "NOT_STARTED": {
            "validator_verdict": "NOT_EXECUTED",
            "workflow_disposition": "WAIT",
            "outcome": "NOT_EXECUTED",
            "reason_code": "AWAITING_EXECUTION",
        },
        "QUEUED": {
            "validator_verdict": "NOT_EXECUTED",
            "workflow_disposition": "WAIT",
            "outcome": "NOT_EXECUTED",
            "reason_code": "AWAITING_EXECUTION",
        },
        "RUNNING": {
            "validator_verdict": "NOT_EXECUTED",
            "workflow_disposition": "WAIT",
            "outcome": "NOT_EXECUTED",
            "reason_code": "AWAITING_EXECUTION",
        },
    }
    try:
        projection = projections[lifecycle]
    except KeyError as error:
        raise ValueError(f"invalid execution lifecycle: {lifecycle}") from error
    return {**projection, "execution_lifecycle": lifecycle}


def project_execution_evidence(
    *,
    lifecycle: str | None,
    receipt_valid: bool,
    capability_admitted: bool,
    outcome_reason_code: str,
    missing_output: bool = False,
) -> dict[str, str]:
    """Mirror Core's surectl operation-evidence projection."""

    if missing_output:
        return {"verdict": "NOT_EXECUTED", "reason_code": "INVALID_CONTRACT"}
    if not receipt_valid or not capability_admitted:
        return {"verdict": "NOT_EXECUTED", "reason_code": outcome_reason_code}
    if lifecycle == "SUCCEEDED":
        return {"verdict": "PASS", "reason_code": "EXECUTION_SUCCEEDED"}
    if lifecycle == "FAILED":
        return {"verdict": "FAIL", "reason_code": "EXECUTION_FAILED"}
    if lifecycle == "PARTIAL":
        return {"verdict": "FAIL", "reason_code": "EXECUTION_PARTIAL"}
    if lifecycle == "CANCELLED":
        return {"verdict": "NOT_EXECUTED", "reason_code": "EXECUTION_CANCELLED"}
    return {"verdict": "NOT_EXECUTED", "reason_code": outcome_reason_code}


def _admission_status(*, reason_code: str, probe_invoked: bool, execute_invoked: bool) -> str:
    if reason_code == "CAPABILITY_MISSING":
        return "CAPABILITY_MISSING"
    if not probe_invoked and not execute_invoked:
        return "REJECTED"
    if reason_code == "INVALID_CONTRACT" and not execute_invoked:
        return "REJECTED"
    return "ADMITTED"


def create_execution_admission_trace(
    request: Mapping[str, Any],
    *,
    observed_at: str,
    outcome_reason_code: str,
    probe_invoked: bool,
    execute_invoked: bool,
    receipt: Mapping[str, Any] | None = None,
    receipt_valid: bool = False,
) -> dict[str, Any]:
    """Build a preflight trace without inventing executor identity.

    ``execution_receipt.v1`` remains the historical launch artifact.  This
    separate document answers the earlier question: did the request reach an
    admitted adapter, and was a receipt actually present/valid?
    """

    runtime = request.get("runtime_requirements") if isinstance(request.get("runtime_requirements"), Mapping) else {}
    trace: dict[str, Any] = {
        "schema": ADMISSION_SCHEMA,
        "request_digest": digest_json(dict(request)),
        "status": _admission_status(
            reason_code=outcome_reason_code,
            probe_invoked=probe_invoked,
            execute_invoked=execute_invoked,
        ),
        "reason_code": outcome_reason_code,
        "observed_at": observed_at,
        "probe_invoked": bool(probe_invoked),
        "execute_invoked": bool(execute_invoked),
        "receipt_present": receipt is not None,
        "receipt_valid": bool(receipt_valid),
    }
    request_id = request.get("request_id")
    if isinstance(request_id, str) and ID_RE.fullmatch(request_id):
        trace["request_id"] = request_id
    executor_kind = runtime.get("executor_kind") if isinstance(runtime, Mapping) else None
    if isinstance(executor_kind, str):
        trace["requested_executor_kind"] = executor_kind
    surface = runtime.get("execution_surface") if isinstance(runtime, Mapping) else None
    if surface in EXECUTION_ADAPTER_SURFACES:
        trace["execution_surface"] = surface
    adapter_digest = request.get("adapter_manifest_digest")
    if valid_digest(adapter_digest):
        trace["adapter_manifest_digest"] = str(adapter_digest)
    return trace


def validate_execution_admission_trace(value: object) -> list[str]:
    """Validate the host-neutral preflight trace wire shape."""

    if not isinstance(value, Mapping):
        return ["execution admission trace must be an object"]
    errors: list[str] = []
    if value.get("schema") != ADMISSION_SCHEMA:
        errors.append("admission.schema is unsupported")
    if not valid_digest(value.get("request_digest")):
        errors.append("admission.request_digest must be a SHA-256 digest")
    request_id = value.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or ID_RE.fullmatch(request_id) is None):
        errors.append("admission.request_id is invalid")
    if value.get("requested_executor_kind") is not None and not isinstance(value.get("requested_executor_kind"), str):
        errors.append("admission.requested_executor_kind must be a string")
    if value.get("execution_surface") is not None and value.get("execution_surface") not in EXECUTION_ADAPTER_SURFACES:
        errors.append("admission.execution_surface is invalid")
    if value.get("adapter_manifest_digest") is not None and not valid_digest(value.get("adapter_manifest_digest")):
        errors.append("admission.adapter_manifest_digest must be a SHA-256 digest")
    if value.get("status") not in ADMISSION_STATUSES:
        errors.append("admission.status is invalid")
    if not isinstance(value.get("reason_code"), str) or not str(value.get("reason_code")).strip():
        errors.append("admission.reason_code must be a non-empty string")
    if not isinstance(value.get("observed_at"), str) or not str(value.get("observed_at")).strip():
        errors.append("admission.observed_at must be a non-empty string")
    for field in ("probe_invoked", "execute_invoked", "receipt_present", "receipt_valid"):
        if not isinstance(value.get(field), bool):
            errors.append(f"admission.{field} must be boolean")
    if value.get("receipt_valid") is True and value.get("receipt_present") is not True:
        errors.append("admission.receipt_valid requires receipt_present")
    if value.get("status") == "CAPABILITY_MISSING" and value.get("reason_code") != "CAPABILITY_MISSING":
        errors.append("CAPABILITY_MISSING admission must use reason_code CAPABILITY_MISSING")
    if value.get("status") == "CAPABILITY_MISSING" and value.get("execute_invoked") is True:
        errors.append("CAPABILITY_MISSING admission cannot invoke execute")
    if value.get("status") == "REJECTED" and value.get("reason_code") == "CAPABILITY_MISSING":
        errors.append("REJECTED admission cannot use reason_code CAPABILITY_MISSING")
    if value.get("status") == "REJECTED" and value.get("execute_invoked") is True:
        errors.append("REJECTED admission cannot invoke execute")
    if value.get("status") == "ADMITTED" and value.get("probe_invoked") is not True and value.get("execute_invoked") is not True:
        errors.append("ADMITTED admission requires probe_invoked or execute_invoked")
    return errors


def validate_execution_admission_binding(request: Mapping[str, Any], trace: Mapping[str, Any]) -> list[str]:
    """Validate a persisted trace against the exact request bytes/identity."""

    if not isinstance(trace, Mapping):
        return validate_execution_admission_trace(trace)
    errors = validate_execution_admission_trace(trace)
    expected = digest_json(dict(request))
    if valid_digest(trace.get("request_digest")) and not same_digest(trace.get("request_digest"), expected):
        errors.append("admission.request_digest does not match request")
    request_id = request.get("request_id")
    if isinstance(request_id, str) and ID_RE.fullmatch(request_id) is not None:
        if trace.get("request_id") is None:
            errors.append("admission.request_id is missing from request binding")
        elif trace.get("request_id") != request_id:
            errors.append("admission.request_id does not match request")
    runtime = request.get("runtime_requirements") if isinstance(request.get("runtime_requirements"), Mapping) else {}
    executor_kind = runtime.get("executor_kind")
    if isinstance(executor_kind, str):
        if trace.get("requested_executor_kind") is None:
            errors.append("admission.requested_executor_kind is missing from request binding")
        elif trace.get("requested_executor_kind") != executor_kind:
            errors.append("admission.requested_executor_kind does not match request")
    surface = runtime.get("execution_surface")
    if surface in EXECUTION_ADAPTER_SURFACES:
        if trace.get("execution_surface") is None:
            errors.append("admission.execution_surface is missing from request binding")
        elif trace.get("execution_surface") != surface:
            errors.append("admission.execution_surface does not match request")
    request_adapter = request.get("adapter_manifest_digest")
    trace_adapter = trace.get("adapter_manifest_digest")
    if valid_digest(request_adapter):
        if trace_adapter is None:
            errors.append("admission.adapter_manifest_digest is missing from request binding")
        elif not same_digest(trace_adapter, request_adapter):
            errors.append("admission.adapter_manifest_digest does not match request")
    return errors


def validate_execution_admission_receipt_binding(
    request: Mapping[str, Any],
    trace: Mapping[str, Any],
    *,
    receipt: Mapping[str, Any] | None = None,
    receipt_valid: bool | None = None,
    capability_admitted: bool | None = None,
) -> list[str]:
    """Bind admission provenance to the receipt and its validation result.

    Admission is an audit record, not an execution receipt.  This check keeps
    the two records from making contradictory claims while leaving lifecycle
    and output validation to :func:`validate_contract_pair`.
    """

    errors = validate_execution_admission_binding(request, trace)
    receipt_present = receipt is not None
    if trace.get("receipt_present") != receipt_present:
        errors.append("admission.receipt_present does not match the persisted receipt")
    if receipt_valid is not None and trace.get("receipt_valid") is not receipt_valid:
        errors.append("admission.receipt_valid does not match receipt validation")
    if capability_admitted is False and trace.get("status") == "ADMITTED":
        errors.append("admission.status ADMITTED conflicts with a non-admitted capability result")
    if capability_admitted is True and trace.get("status") == "CAPABILITY_MISSING":
        errors.append("admission.status CAPABILITY_MISSING conflicts with an admitted capability result")
    if trace.get("receipt_valid") is True and not receipt_present:
        errors.append("admission.receipt_valid requires a persisted receipt")
    if receipt is None:
        return errors

    lifecycle = receipt.get("lifecycle")
    if lifecycle == "SUCCEEDED":
        if trace.get("status") != "ADMITTED":
            errors.append("successful receipt requires ADMITTED admission status")
        if trace.get("execute_invoked") is not True:
            errors.append("successful receipt requires admission.execute_invoked")
        if trace.get("receipt_valid") is not True:
            errors.append("successful receipt requires admission.receipt_valid")
        if receipt_valid is False:
            errors.append("successful receipt cannot have invalid receipt validation")
        if capability_admitted is False:
            errors.append("successful receipt cannot have missing capability")
    if trace.get("status") == "CAPABILITY_MISSING" and lifecycle != "NOT_STARTED":
        errors.append("CAPABILITY_MISSING admission cannot carry an executing receipt")
    if trace.get("status") == "REJECTED" and lifecycle != "NOT_STARTED":
        errors.append("REJECTED admission cannot carry an executing receipt")
    if lifecycle != "NOT_STARTED" and trace.get("execute_invoked") is not True:
        errors.append("an executing receipt requires admission.execute_invoked")
    return errors


def _valid_adapter_timeout(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= MAX_ADAPTER_TIMEOUT_SECONDS
    )


def _utf16_sort_key(value: str) -> bytes:
    """Match JavaScript's UTF-16 code-unit ordering for cross-runtime digests."""

    return value.encode("utf-16-be", errors="surrogatepass")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def digest_text(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def digest_json(value: Any) -> str:
    return digest_text(canonical_json(value))


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def digest_tree(path: Path) -> tuple[str, int]:
    """Return a stable digest and byte size for a regular directory tree."""

    lexical_root = path.expanduser().absolute()
    root_stat = lexical_root.lstat()
    if stat_module.S_ISLNK(root_stat.st_mode):
        raise ValueError(f"output path is a symlink: {lexical_root}")
    root = lexical_root.resolve()
    if not stat_module.S_ISDIR(root_stat.st_mode):
        raise ValueError(f"output path is not a directory: {lexical_root}")
    entries: list[tuple[str, str, str | None, int | None]] = []

    def visit(current: Path) -> int:
        stat = current.lstat()
        if stat_module.S_ISLNK(stat.st_mode):
            raise ValueError(f"output path contains a symlink: {current}")
        relative = current.relative_to(root).as_posix() or "."
        if stat_module.S_ISREG(stat.st_mode):
            entries.append((relative, "file", digest_file(current), stat.st_size))
            return stat.st_size
        if not stat_module.S_ISDIR(stat.st_mode):
            raise ValueError(f"output path is not a regular file or directory: {current}")
        entries.append((relative, "directory", None, None))
        total = 0
        for child in sorted(current.iterdir(), key=lambda item: _utf16_sort_key(item.name)):
            total += visit(child)
        return total

    size = visit(root)
    rows = "".join(
        f"{relative}\0directory\n" if kind == "directory" else f"{relative}\0file\0{digest}\0{entry_size}\n"
        for relative, kind, digest, entry_size in sorted(entries, key=lambda item: _utf16_sort_key(item[0]))
    )
    return digest_text(rows), size


def valid_digest(value: object) -> bool:
    return isinstance(value, str) and bool(DIGEST_RE.fullmatch(value))


def normalize_digest(value: object, *, fallback: Any = None) -> str:
    if valid_digest(value):
        return str(value).lower()
    return digest_json(fallback if fallback is not None else value)


def same_digest(left: object, right: object) -> bool:
    if not valid_digest(left) or not valid_digest(right):
        return False
    return str(left).removeprefix("sha256:").lower() == str(right).removeprefix("sha256:").lower()


def safe_id(value: object, fallback: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(value or "")).strip("-")
    if not candidate or not re.match(r"^[A-Za-z0-9]", candidate):
        candidate = fallback
    return candidate[:128]


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write an artifact atomically within its already-admitted root."""

    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write a history record once; a reused id must have identical bytes."""

    if path.exists():
        existing = read_json(path)
        if canonical_json(existing) != canonical_json(dict(value)):
            raise ValueError(f"execution history id already exists with different content: {path.name}")
        return
    write_json(path, value)


def _file_rows(path: Path) -> list[dict[str, Any]]:
    path = path.expanduser().resolve()
    if path.is_file() and not path.is_symlink():
        return [{"path": str(path), "sha256": digest_file(path), "size": path.stat().st_size}]
    if path.is_dir() and not path.is_symlink():
        rows: list[dict[str, Any]] = []
        for child in sorted(path.rglob("*")):
            if child.is_file() and not child.is_symlink():
                rows.append({"path": str(child), "sha256": digest_file(child), "size": child.stat().st_size})
        return rows
    return [{"path": str(path), "missing": True}]


def snapshot_digest(paths: Iterable[Path]) -> str:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(_file_rows(path))
    return digest_json(sorted(rows, key=lambda row: str(row.get("path", ""))))


def _source_root(path: Path, source_root: Path | None) -> Path:
    return (source_root or path.parent).expanduser().resolve()


def artifact_ref(
    path: Path,
    *,
    origin: str = "generated",
    source_root: Path | None = None,
    reference_snapshot_digest: str | None = None,
    artifact_id: str | None = None,
) -> dict[str, Any]:
    """Build a strict artifact reference for an existing regular file or directory."""

    lexical = path.expanduser().absolute()
    stat = lexical.lstat() if lexical.exists() or lexical.is_symlink() else None
    resolved = lexical.resolve()
    if stat is None or stat_module.S_ISLNK(stat.st_mode) or not (
        stat_module.S_ISREG(stat.st_mode) or stat_module.S_ISDIR(stat.st_mode)
    ):
        raise FileNotFoundError(resolved)
    if origin not in {"local_staging", "read_only_reference", "generated", "external"}:
        raise ValueError(f"unsupported artifact origin: {origin}")
    if stat_module.S_ISDIR(stat.st_mode):
        sha256, size = digest_tree(resolved)
        kind = "directory"
        digest_kind = "tree_sha256"
        media_type = "inode/directory"
    else:
        sha256 = digest_file(resolved)
        size = stat.st_size
        kind = "file"
        digest_kind = "file_sha256"
        media_type = "application/json" if resolved.suffix.lower() == ".json" else "application/octet-stream"
    result: dict[str, Any] = {
        "artifact_id": safe_id(artifact_id, f"artifact-{hashlib.sha256(str(resolved).encode()).hexdigest()[:12]}"),
        "path": str(lexical),
        "resolved_path": str(resolved),
        "sha256": sha256,
        "size": size,
        "media_type": media_type,
        "origin": origin,
        "source_root": str(_source_root(resolved, source_root)),
    }
    # Preserve the historical file-artifact wire shape. Directory artifacts
    # need explicit metadata because their digest is a tree digest; legacy
    # consumers and Core validation treat omitted fields as file defaults.
    if kind == "directory":
        result["kind"] = kind
        result["digest_kind"] = digest_kind
    if origin == "read_only_reference":
        result["reference_snapshot_digest"] = normalize_digest(
            reference_snapshot_digest, fallback={"path": str(resolved), "sha256": result["sha256"]}
        )
    return result


def capability_evidence(
    capability_id: str,
    *,
    status: str,
    capability_class: str = "execution_capability",
    source: str = "executor",
    details: Mapping[str, Any] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Create capability evidence without treating an unprobed capability as available."""

    if status not in {"AVAILABLE", "MISSING", "UNKNOWN", "DENIED"}:
        raise ValueError(f"invalid capability status: {status}")
    evidence: dict[str, Any] = {
        "capability_id": safe_id(capability_id, "sure.execution.unknown"),
        "capability_class": capability_class,
        "status": status,
        "source": source,
        "observed_at": observed_at or utc_now(),
    }
    if details:
        evidence["details"] = dict(details)
    if status == "AVAILABLE":
        evidence["evidence_digest"] = digest_json(evidence)
    return evidence


def validate_capability_evidence(value: object, *, field: str = "capability_evidence") -> list[str]:
    """Validate one untrusted capability-evidence record.

    Execution capability admission must never depend on an arbitrary source
    string being merely different from ``agent``. Keep this wire check in the
    Python bridge in lockstep with the TypeScript Core validator.
    """

    if not isinstance(value, Mapping):
        return [f"{field} must be an object"]
    errors: list[str] = []
    capability_id = value.get("capability_id")
    if not isinstance(capability_id, str) or CAPABILITY_ID_RE.fullmatch(capability_id) is None:
        errors.append(f"{field}.capability_id is invalid")
    capability_class = value.get("capability_class")
    if capability_class not in CAPABILITY_CLASSES:
        errors.append(f"{field}.capability_class is invalid")
    status = value.get("status")
    if status not in CAPABILITY_STATUSES:
        errors.append(f"{field}.status is invalid")
    source = value.get("source")
    if source not in CAPABILITY_SOURCES:
        errors.append(f"{field}.source is invalid")
    if capability_class == "execution_capability" and source == "agent":
        errors.append(f"{field}.source agent cannot satisfy an execution capability")
    observed_at = value.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at.strip():
        errors.append(f"{field}.observed_at must be a non-empty string")
    evidence_digest = value.get("evidence_digest")
    if evidence_digest is not None and not valid_digest(evidence_digest):
        errors.append(f"{field}.evidence_digest must be a SHA-256 digest when present")
    if status == "AVAILABLE" and not valid_digest(evidence_digest):
        errors.append(f"{field}.evidence_digest is required for AVAILABLE evidence")
    if value.get("details") is not None and not isinstance(value.get("details"), Mapping):
        errors.append(f"{field}.details must be an object when present")
    return errors


def validate_capability_evidence_list(value: object, *, field: str = "capability_evidence") -> list[str]:
    """Validate an evidence array received from an executor or receipt."""

    if not isinstance(value, list):
        return [f"{field} must be an array"]
    errors: list[str] = []
    for index, entry in enumerate(value):
        errors.extend(validate_capability_evidence(entry, field=f"{field}[{index}]"))
    return errors


def capability_summary(
    requirements: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]]
) -> dict[str, list[str] | bool]:
    by_id: dict[str, Mapping[str, Any]] = {}
    missing: list[str] = []
    unknown: list[str] = []
    denied: list[str] = []
    invalid: list[str] = []
    for item in evidence:
        if not isinstance(item, Mapping):
            invalid.append("")
            continue
        by_id[str(item.get("capability_id"))] = item
        if validate_capability_evidence(item):
            invalid.append(str(item.get("capability_id") or ""))
    for requirement in requirements:
        if requirement.get("required") is not True:
            continue
        capability_id = str(requirement.get("capability_id") or "")
        item = by_id.get(capability_id)
        if item is None or item.get("status") == "MISSING":
            missing.append(capability_id)
        elif item.get("status") == "UNKNOWN":
            unknown.append(capability_id)
        elif item.get("status") == "DENIED":
            denied.append(capability_id)
        elif item.get("status") != "AVAILABLE":
            invalid.append(capability_id)
        elif not valid_digest(item.get("evidence_digest")):
            invalid.append(capability_id)
    return {
        "admitted": not (missing or unknown or denied or invalid),
        "missing": missing,
        "unknown": unknown,
        "denied": denied,
        "invalid": invalid,
    }


def validate_adapter_route(runtime_requirements: Mapping[str, Any]) -> list[str]:
    """Validate the optional external transport route in a v1 request.

    The Python bridge mirrors the Core parser so legacy scripts can continue
    writing their historical views while rejecting a VC/remote request that
    could otherwise be silently executed by a local fallback.
    """

    surface = runtime_requirements.get("execution_surface")
    if surface is None:
        if runtime_requirements.get("executor_kind") in {"remote", "trusted"}:
            return ["external executor kind requires runtime_requirements.execution_surface"]
        return []
    errors: list[str] = []
    if not isinstance(surface, str) or surface not in EXECUTION_ADAPTER_SURFACES:
        return ["runtime_requirements.execution_surface must be vc, remote, or trusted"]
    executor_kind = runtime_requirements.get("executor_kind")
    if not isinstance(executor_kind, str) or executor_kind not in EXECUTION_ADAPTER_KINDS[surface]:
        allowed = " or ".join(sorted(EXECUTION_ADAPTER_KINDS[surface]))
        errors.append(
            f"runtime_requirements.executor_kind must be {allowed} for execution_surface={surface}"
        )
    if surface == "vc":
        for field in ("vc_project", "vc_partition"):
            value = runtime_requirements.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"runtime_requirements.{field} must be a non-empty string")
        for field in ("vc_gpus", "vc_memory_gb", "vc_cpus"):
            value = runtime_requirements.get(field)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
                errors.append(f"runtime_requirements.{field} must be a positive integer when present")
    adapter_timeouts = runtime_requirements.get("adapter_timeouts")
    if adapter_timeouts is not None:
        timeout_fields = ("submit_seconds", "wait_seconds", "command_seconds", "cancel_seconds", "poll_seconds")
        if not isinstance(adapter_timeouts, Mapping):
            errors.append("runtime_requirements.adapter_timeouts must be an object")
        else:
            for field in sorted(set(adapter_timeouts) - set(timeout_fields)):
                errors.append(f"runtime_requirements.adapter_timeouts has unknown field {field}")
            for field in timeout_fields:
                value = adapter_timeouts.get(field)
                if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
                    errors.append(f"runtime_requirements.adapter_timeouts.{field} must be a positive integer when present")
                if (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value > MAX_ADAPTER_TIMEOUT_SECONDS
                ):
                    errors.append(f"runtime_requirements.adapter_timeouts.{field} exceeds the maximum allowed value")
            command = adapter_timeouts.get("command_seconds")
            wait = adapter_timeouts.get("wait_seconds")
            poll = adapter_timeouts.get("poll_seconds")
            if _valid_adapter_timeout(command) and _valid_adapter_timeout(wait) and command > wait:
                errors.append("runtime_requirements.adapter_timeouts.command_seconds must not exceed wait_seconds")
            if _valid_adapter_timeout(poll) and _valid_adapter_timeout(wait) and poll > wait:
                errors.append("runtime_requirements.adapter_timeouts.poll_seconds must not exceed wait_seconds")
    return errors


def _relative_contract_path(value: object, field: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        errors.append(f"{field} must be a non-empty relative POSIX path")
        return None
    normalized = posixpath.normpath(value)
    if normalized in {".", ".."} or normalized.startswith("../") or normalized != value:
        errors.append(f"{field} must be normalized and non-escaping: {value}")
        return None
    return normalized


def validate_output_contract(value: object) -> list[str]:
    """Validate the declarative output contract without touching the filesystem."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return ["execution output contract must be an object"]
    if value.get("schema") != OUTPUT_CONTRACT_SCHEMA:
        errors.append("output contract schema is unsupported")
    mode = value.get("mode")
    if mode not in OUTPUT_MODES:
        errors.append("output contract mode is invalid")
    outputs = value.get("outputs")
    output_paths: set[str] = set()
    output_ids: set[str] = set()
    if not isinstance(outputs, list) or not outputs:
        errors.append("output contract must declare at least one output")
    else:
        for index, raw in enumerate(outputs):
            field = f"output_contract.outputs[{index}]"
            if not isinstance(raw, Mapping):
                errors.append(f"{field} must be an object")
                continue
            artifact_id = raw.get("artifact_id")
            if not isinstance(artifact_id, str) or not ID_RE.fullmatch(artifact_id):
                errors.append(f"{field}.artifact_id is invalid")
            elif artifact_id in output_ids:
                errors.append(f"{field}.artifact_id is duplicated")
            else:
                output_ids.add(artifact_id)
            path = _relative_contract_path(raw.get("path"), f"{field}.path", errors)
            if path is not None:
                if path in output_paths:
                    errors.append(f"{field}.path is duplicated")
                else:
                    output_paths.add(path)
            if raw.get("kind") not in OUTPUT_KINDS:
                errors.append(f"{field}.kind must be file or directory")
            if not isinstance(raw.get("required"), bool):
                errors.append(f"{field}.required must be boolean")
        if mode == "producing" and not any(isinstance(raw, Mapping) and raw.get("required") is True for raw in outputs):
            errors.append("producing output contract must declare a required output")
    temporary = value.get("temporary_paths")
    if not isinstance(temporary, list):
        errors.append("output_contract.temporary_paths must be an array")
    else:
        temporary_paths: set[str] = set()
        for index, raw in enumerate(temporary):
            path = _relative_contract_path(raw, f"output_contract.temporary_paths[{index}]", errors)
            if path is not None:
                if path in temporary_paths:
                    errors.append(f"output_contract.temporary_paths[{index}] is duplicated")
                else:
                    temporary_paths.add(path)
    if not isinstance(value.get("allow_missing_on_failure"), bool):
        errors.append("output_contract.allow_missing_on_failure must be boolean")
    if not isinstance(value.get("retain_failed_outputs"), bool):
        errors.append("output_contract.retain_failed_outputs must be boolean")
    return errors


def output_contract_digest(contract: Mapping[str, Any]) -> str:
    return digest_json(dict(contract))


def output_set_digest(
    outputs: Sequence[Mapping[str, Any]], residuals: Sequence[Mapping[str, Any]] = ()
) -> str:
    sorted_outputs = sorted(
        (dict(item) for item in outputs),
        key=lambda item: _utf16_sort_key(f"{item.get('artifact_id', '')}\0{item.get('resolved_path', '')}"),
    )
    sorted_residuals = sorted(
        (dict(item) for item in residuals),
        key=lambda item: _utf16_sort_key(f"{item.get('path', '')}\0{item.get('resolved_path', '')}"),
    )
    return digest_json({"schema": OUTPUT_SET_SCHEMA, "outputs": sorted_outputs, "residuals": sorted_residuals})


def _output_relative(request: Mapping[str, Any], path: object, *, root_key: str = "resolved_path") -> str | None:
    if not isinstance(path, str) or not path:
        return None
    output_root = request.get("output_root") if isinstance(request.get("output_root"), Mapping) else {}
    try:
        root = Path(os.path.abspath(os.path.expanduser(str(output_root.get(root_key) or ""))))
        candidate = Path(os.path.abspath(os.path.expanduser(path)))
        relative = candidate.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    return relative if relative and relative != "." else None


def _validate_output_digest_shape(output: Mapping[str, Any], field: str, expected_kind: str, errors: list[str]) -> None:
    kind = output.get("kind", "file")
    if kind != expected_kind:
        errors.append(f"{field}.kind does not match declared output kind")
    expected_digest_kind = "tree_sha256" if expected_kind == "directory" else "file_sha256"
    if output.get("digest_kind", "file_sha256") != expected_digest_kind:
        errors.append(f"{field}.digest_kind must be {expected_digest_kind} for a {expected_kind} output")
    if expected_kind == "directory" and output.get("media_type") != "inode/directory":
        errors.append(f"{field}.media_type must be inode/directory for a directory output")


def _validate_residual(
    residual: object,
    index: int,
    request: Mapping[str, Any],
    contract: Mapping[str, Any],
    errors: list[str],
) -> None:
    field = f"receipt.residuals[{index}]"
    if not isinstance(residual, Mapping):
        errors.append(f"{field} must be an object")
        return
    for key in ("path", "resolved_path"):
        if not isinstance(residual.get(key), str) or not residual.get(key):
            errors.append(f"{field}.{key} must be a non-empty string")
    kind = residual.get("kind")
    if kind not in OUTPUT_KINDS:
        errors.append(f"{field}.kind must be file or directory")
        return
    if residual.get("status") not in {"present", "missing"}:
        errors.append(f"{field}.status must be present or missing")
    relative = _output_relative(request, residual.get("resolved_path"), root_key="resolved_path")
    temporary = contract.get("temporary_paths") if isinstance(contract.get("temporary_paths"), list) else []
    if relative is None or not any(isinstance(root, str) and (relative == root or relative.startswith(f"{root}/")) for root in temporary):
        errors.append(f"{field}.resolved_path is not declared as a temporary path")
    lexical_relative = _output_relative(request, residual.get("path"), root_key="path")
    if lexical_relative is None or relative is None or lexical_relative != relative:
        errors.append(f"{field}.path does not resolve to resolved_path within the output root")
    if residual.get("status") == "present":
        if not valid_digest(residual.get("sha256")):
            errors.append(f"{field}.sha256 is required for present residuals")
        expected = "tree_sha256" if kind == "directory" else "file_sha256"
        if residual.get("digest_kind") != expected:
            errors.append(f"{field}.digest_kind must be {expected} for a present residual")
        if not isinstance(residual.get("size"), int) or residual.get("size") < 0:
            errors.append(f"{field}.size is required for present residuals")
    elif any(key in residual for key in ("sha256", "digest_kind", "size")):
        errors.append(f"{field} missing residuals cannot carry digest or size")


def validate_output_binding(request: Mapping[str, Any], receipt: Mapping[str, Any]) -> list[str]:
    contract = request.get("output_contract")
    has_extension = any(key in receipt for key in ("output_contract_digest", "output_set_digest", "residuals"))
    if contract is None:
        return ["receipt carries output-contract fields without a request output_contract"] if has_extension else []
    contract_errors = validate_output_contract(contract)
    if contract_errors:
        return contract_errors
    assert isinstance(contract, Mapping)
    errors: list[str] = []
    expected_contract_digest = output_contract_digest(contract)
    if not valid_digest(receipt.get("output_contract_digest")):
        errors.append("receipt.output_contract_digest is required when request.output_contract is present")
    elif not same_digest(receipt.get("output_contract_digest"), expected_contract_digest):
        errors.append("receipt.output_contract_digest does not match request.output_contract")
    outputs = receipt.get("outputs") if isinstance(receipt.get("outputs"), list) else []
    specs = {
        str(item.get("artifact_id")): item
        for item in contract.get("outputs", [])
        if isinstance(item, Mapping) and isinstance(item.get("artifact_id"), str)
    }
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    output_root = request.get("output_root") if isinstance(request.get("output_root"), Mapping) else {}
    root = Path(os.path.abspath(os.path.expanduser(str(output_root.get("resolved_path") or ""))))
    for index, raw in enumerate(outputs):
        field = f"receipt.outputs[{index}]"
        if not isinstance(raw, Mapping):
            continue
        artifact_id = str(raw.get("artifact_id") or "")
        if artifact_id in seen_ids:
            errors.append(f"{field}.artifact_id is duplicated")
        seen_ids.add(artifact_id)
        spec = specs.get(artifact_id)
        if spec is None:
            errors.append(f"{field}.artifact_id is not declared by output_contract")
            continue
        relative = _output_relative(request, raw.get("resolved_path"), root_key="resolved_path")
        if relative is None:
            errors.append(f"{field}.resolved_path is outside the output root")
        else:
            if relative in seen_paths:
                errors.append(f"{field}.resolved_path is duplicated")
            seen_paths.add(relative)
            if relative != spec.get("path"):
                errors.append(f"{field}.resolved_path does not match output_contract path {spec.get('path')}")
            try:
                if Path(os.path.abspath(str(raw.get("resolved_path")))) != (root / str(spec.get("path"))).absolute():
                    errors.append(f"{field}.resolved_path is not the declared output path")
            except (OSError, TypeError):
                errors.append(f"{field}.resolved_path is invalid")
        lexical_relative = _output_relative(request, raw.get("path"), root_key="path")
        if lexical_relative is None or lexical_relative != spec.get("path"):
            errors.append(f"{field}.path does not match output_contract path {spec.get('path')}")
        _validate_output_digest_shape(raw, field, str(spec.get("kind")), errors)
    lifecycle = receipt.get("lifecycle")
    terminal = lifecycle in TERMINAL_LIFECYCLES
    for artifact_id, spec in specs.items():
        if spec.get("required") is True and artifact_id not in seen_ids and (
            lifecycle == "SUCCEEDED" or (terminal and contract.get("allow_missing_on_failure") is not True)
        ):
            errors.append(f"required output {artifact_id} is missing from receipt")
    residuals = receipt.get("residuals") if isinstance(receipt.get("residuals"), list) else []
    if lifecycle == "SUCCEEDED" and residuals:
        errors.append("successful execution cannot retain output residuals")
    if residuals and contract.get("retain_failed_outputs") is not True:
        errors.append("receipt residuals are not permitted by output_contract")
    seen_residuals: set[tuple[str, str]] = set()
    for index, residual in enumerate(residuals):
        if isinstance(residual, Mapping):
            key = (str(residual.get("path")), str(residual.get("resolved_path")))
            if key in seen_residuals:
                errors.append(f"receipt.residuals[{index}] is duplicated")
            seen_residuals.add(key)
        _validate_residual(residual, index, request, contract, errors)
    try:
        observed_digest = output_set_digest(outputs, residuals)
    except (TypeError, ValueError) as error:
        errors.append(f"receipt output set cannot be canonicalized: {error}")
        observed_digest = None
    if not valid_digest(receipt.get("output_set_digest")):
        errors.append("receipt.output_set_digest is required when request.output_contract is present")
    elif observed_digest is not None and not same_digest(receipt.get("output_set_digest"), observed_digest):
        errors.append("receipt.output_set_digest does not match observed outputs and residuals")
    return errors


def _subject_defaults(subject: Mapping[str, Any] | None, *, run_id: str, unit_id: str) -> dict[str, Any]:
    value = dict(subject or {})
    manifest = Path(str(value.get("bundle_manifest_path") or f"/tmp/sure/{run_id}/bundle.json")).expanduser()
    if not manifest.is_absolute():
        manifest = Path.cwd() / manifest
    value["bundle_manifest_path"] = str(manifest.resolve())
    value["bundle_digest"] = normalize_digest(value.get("bundle_digest"), fallback={"run_id": run_id, "unit_id": unit_id})
    value["runtime_identity_digest"] = normalize_digest(
        value.get("runtime_identity_digest"), fallback={"runtime": "legacy-unverified", "run_id": run_id}
    )
    for key in ("inference_protocol_digest", "dataset_identity_digest", "scoring_protocol_digest"):
        if value.get(key) is not None:
            value[key] = normalize_digest(value[key], fallback={"field": key, "value": value[key]})
    return value


def build_request(
    *,
    run_id: str,
    unit_id: str,
    operation: str,
    entrypoint: Mapping[str, Any],
    output_root: Path,
    subject: Mapping[str, Any] | None = None,
    inputs: Sequence[Mapping[str, Any]] = (),
    capability_requirements: Sequence[Mapping[str, Any]] = (),
    runtime_requirements: Mapping[str, Any] | None = None,
    attempt: int = 1,
    policy_digest: str | None = None,
    policy_snapshot_digest: str | None = None,
    adapter_manifest_digest: str | None = None,
    reference_snapshot_digest: str | None = None,
    request_id: str | None = None,
    created_at: str | None = None,
    output_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a v1 request, deriving only auditable compatibility digests."""

    run_id = safe_id(run_id, "sure-run")
    unit_id = safe_id(unit_id, "execution")
    root = output_root.expanduser().resolve()
    entry = dict(entrypoint)
    executable = str(entry.get("executable") or "")
    argv = entry.get("argv") if isinstance(entry.get("argv"), list) else []
    entrypoint_value: dict[str, Any] = {
        "executable": executable,
        "argv": [str(value) for value in argv],
    }
    if entry.get("working_directory"):
        entrypoint_value["working_directory"] = str(Path(str(entry["working_directory"])).expanduser().resolve())
    policy = normalize_digest(
        policy_digest or os.environ.get("SURE_POLICY_DIGEST"),
        fallback={"compatibility": "legacy-unverified", "output_root": str(root)},
    )
    policy_snapshot = policy_snapshot_digest or os.environ.get("SURE_POLICY_SNAPSHOT_DIGEST")
    adapter_manifest = adapter_manifest_digest or os.environ.get("SURE_ADAPTER_MANIFEST_DIGEST")
    snapshot = normalize_digest(reference_snapshot_digest, fallback={"inputs": list(inputs), "root": str(root)})
    output_binding = {
        "path": str(output_root.expanduser().absolute()),
        "resolved_path": str(root),
        "scope_id": safe_id(run_id, "sure-run"),
        "policy_digest": policy,
        "writable": True,
    }
    request: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "request_id": safe_id(request_id, f"{run_id}-{unit_id}-request-{uuid.uuid4().hex[:8]}"),
        "semantic_request_digest": "",
        "run_id": run_id,
        "unit_id": unit_id,
        "attempt": max(1, int(attempt)),
        "operation": operation,
        "subject": _subject_defaults(subject, run_id=run_id, unit_id=unit_id),
        "inputs": [dict(item) for item in inputs],
        "entrypoint": entrypoint_value,
        "runtime_requirements": dict(runtime_requirements or {}),
        "capability_requirements": [dict(item) for item in capability_requirements],
        "reference_snapshot_digest": snapshot,
        "output_root": output_binding,
        "policy_digest": policy,
        "created_at": created_at or utc_now(),
    }
    if policy_snapshot:
        # Unlike legacy compatibility digests, a supplied policy snapshot must
        # remain auditable. Preserve malformed values so the contract validator
        # can fail closed instead of silently hashing an absent/forged snapshot.
        request["policy_snapshot_digest"] = (
            policy_snapshot.lower() if valid_digest(policy_snapshot) else str(policy_snapshot)
        )
    if adapter_manifest:
        request["adapter_manifest_digest"] = (
            adapter_manifest.lower() if valid_digest(adapter_manifest) else str(adapter_manifest)
        )
    if output_contract is not None:
        request["output_contract"] = dict(output_contract)
    semantic = dict(request)
    semantic.pop("request_id", None)
    semantic.pop("created_at", None)
    semantic["semantic_request_digest"] = ""
    request["semantic_request_digest"] = digest_json(semantic)
    return request


def executor_digest(kind: str, version: str, *, explicit: str | None = None) -> str:
    return normalize_digest(explicit or os.environ.get("SURE_EXECUTOR_DIGEST"), fallback={"kind": kind, "version": version})


def build_receipt(
    request: Mapping[str, Any],
    *,
    lifecycle: str,
    executor_kind: str,
    executor_version: str = "sure-python-bridge.v1",
    executor_digest_value: str | None = None,
    capability_evidence_values: Sequence[Mapping[str, Any]] = (),
    outputs: Sequence[Mapping[str, Any]] = (),
    started_at: str | None = None,
    finished_at: str | None = None,
    exit_code: int | None = None,
    diagnostics: Sequence[Mapping[str, Any]] = (),
    receipt_id: str | None = None,
    residuals: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if lifecycle not in LIFECYCLES:
        raise ValueError(f"invalid execution lifecycle: {lifecycle}")
    request_dict = dict(request)
    started = started_at or utc_now()
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": safe_id(receipt_id, f"{request_dict.get('request_id', 'request')}-receipt-{uuid.uuid4().hex[:8]}"),
        "request_id": str(request_dict.get("request_id") or "request"),
        "request_digest": digest_json(request_dict),
        "semantic_request_digest": str(request_dict.get("semantic_request_digest") or ""),
        "run_id": str(request_dict.get("run_id") or "run"),
        "unit_id": str(request_dict.get("unit_id") or "execution"),
        "attempt": int(request_dict.get("attempt") or 1),
        "executor": {
            "executor_id": safe_id(f"sure-python-{executor_kind}", "sure-python").lower(),
            "kind": executor_kind,
            "version": executor_version,
            "digest": executor_digest(executor_kind, executor_version, explicit=executor_digest_value),
            "trust_level": "cooperative",
        },
        "lifecycle": lifecycle,
        "capability_evidence": [dict(item) for item in capability_evidence_values],
        "outputs": [dict(item) for item in outputs],
        "reference_snapshot_digest": str(request_dict.get("reference_snapshot_digest") or ""),
        "output_root": dict(request_dict.get("output_root") or {}),
        "policy_digest": str(request_dict.get("policy_digest") or ""),
        "started_at": started,
    }
    if request_dict.get("policy_snapshot_digest") is not None:
        receipt["policy_snapshot_digest"] = str(request_dict["policy_snapshot_digest"])
    if request_dict.get("adapter_manifest_digest") is not None:
        receipt["adapter_manifest_digest"] = str(request_dict["adapter_manifest_digest"])
    if lifecycle in TERMINAL_LIFECYCLES:
        receipt["finished_at"] = finished_at or utc_now()
    if exit_code is not None:
        receipt["exit_code"] = int(exit_code)
    if diagnostics:
        receipt["diagnostics"] = [dict(item) for item in diagnostics]
    if isinstance(request_dict.get("output_contract"), Mapping):
        receipt["output_contract_digest"] = output_contract_digest(request_dict["output_contract"])
        receipt["residuals"] = [dict(item) for item in residuals]
        receipt["output_set_digest"] = output_set_digest(receipt["outputs"], receipt["residuals"])
    return receipt


def map_vc_job_result_to_receipt(
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    executor_id: str | None = None,
    executor_version: str = "sure-vc-adapter.v1",
    executor_digest_value: str | None = None,
    executor_trust_level: str = "cooperative",
    capability_evidence_values: Sequence[Mapping[str, Any]] = (),
    outputs: Sequence[Mapping[str, Any]] = (),
    submitted: bool = True,
    capability_available: bool = True,
    cancellation_confirmed: bool | None = None,
    observed_at: str | None = None,
    receipt_id: str | None = None,
) -> dict[str, Any]:
    """Map a normalized VC job result into the neutral receipt contract.

    This is deliberately a pure adapter boundary: it does not submit, poll, or
    cancel a job, and it never changes workflow state.  A timeout is reported
    as ``CANCELLED`` with explicit cancellation uncertainty; a submit failure
    or missing capability is ``NOT_STARTED``.  Neither case can become a
    successful receipt merely because a job id or capability claim is present.
    """

    runtime = request.get("runtime_requirements")
    if not isinstance(runtime, Mapping) or runtime.get("execution_surface") != "vc":
        raise ValueError("VC receipt mapping requires runtime_requirements.execution_surface=vc")
    executor_kind = runtime.get("executor_kind")
    if executor_kind not in {"remote", "trusted"}:
        raise ValueError("VC receipt mapping requires a remote or trusted executor kind")
    if executor_trust_level not in {"cooperative", "host_enforced", "attested"}:
        raise ValueError("executor_trust_level is invalid")

    job_id_value = result.get("job_id")
    partition_value = result.get("partition")
    for field, value in (("job_id", job_id_value), ("partition", partition_value)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"VC result {field} must be a non-empty string when present")
    job_id = str(job_id_value).strip() if isinstance(job_id_value, str) else None
    partition = str(partition_value).strip() if isinstance(partition_value, str) else None
    if capability_available and submitted and (job_id is None or partition is None):
        raise ValueError("submitted VC result requires job_id and partition")
    timed_out = result.get("timed_out") is True
    exit_code = result.get("exit_code")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise ValueError("VC result exit_code must be an integer or null")

    diagnostics: list[dict[str, Any]] = []
    lifecycle: str
    receipt_exit_code: int | None = None
    if not capability_available:
        lifecycle = "NOT_STARTED"
        diagnostics.append({"code": "CAPABILITY_MISSING", "message": "VC capability was not available"})
    elif not submitted:
        lifecycle = "NOT_STARTED"
        diagnostics.append({"code": "EXECUTOR_SPAWN_FAILED", "message": "VC job submission did not start"})
    elif timed_out or exit_code is None:
        lifecycle = "CANCELLED"
        diagnostics.append(
            {
                "code": "EXECUTOR_TIMEOUT" if timed_out else "EXECUTOR_NO_EXIT_CODE",
                "message": "VC job did not produce a terminal exit code before the adapter deadline",
            }
        )
        if cancellation_confirmed is True:
            diagnostics.append({"code": "CANCEL_CONFIRMED", "message": "VC cancellation was confirmed"})
        else:
            diagnostics.append(
                {
                    "code": "CANCEL_UNCONFIRMED",
                    "message": "VC cancellation was best-effort and was not authoritatively confirmed",
                }
            )
    elif exit_code == 0:
        lifecycle = "SUCCEEDED"
        receipt_exit_code = 0
    else:
        lifecycle = "FAILED"
        receipt_exit_code = exit_code
        diagnostics.append(
            {"code": "EXECUTION_FAILED", "message": f"VC job exited with code {exit_code}"}
        )

    metadata: dict[str, Any] = {}
    if job_id is not None:
        metadata["job_id"] = job_id
    if partition is not None:
        metadata["partition"] = partition
    for field in ("duration_ms", "log_dir", "submit_command"):
        if result.get(field) is not None:
            metadata[field] = result[field]
    if isinstance(result.get("vc_diagnostics"), str) and result["vc_diagnostics"].strip():
        metadata["vc_diagnostics"] = result["vc_diagnostics"]
    for key in ("stdout", "stderr"):
        value = result.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value[-8192:]
    diagnostics.append({"code": "VC_JOB_METADATA", "message": "normalized VC job metadata", "details": metadata})

    evidence = [dict(item) for item in capability_evidence_values]
    if not evidence:
        observed = str(observed_at or utc_now())
        for requirement in request.get("capability_requirements") or []:
            if not isinstance(requirement, Mapping):
                continue
            capability_id = requirement.get("capability_id")
            capability_class = requirement.get("capability_class")
            if not isinstance(capability_id, str) or not isinstance(capability_class, str):
                continue
            evidence.append(
                {
                    "capability_id": capability_id,
                    "capability_class": capability_class,
                    "status": "AVAILABLE" if capability_available else "MISSING",
                    "source": "executor",
                    "observed_at": observed,
                    "evidence_digest": digest_json(
                        {
                            "capability_id": capability_id,
                            "job_id": job_id,
                            "available": capability_available,
                        }
                    ),
                }
            )

    receipt = build_receipt(
        request,
        lifecycle=lifecycle,
        executor_kind=str(executor_kind),
        executor_version=executor_version,
        executor_digest_value=executor_digest_value,
        capability_evidence_values=evidence,
        outputs=outputs,
        started_at=str(observed_at or utc_now()),
        finished_at=str(observed_at or utc_now()) if lifecycle in TERMINAL_LIFECYCLES else None,
        exit_code=receipt_exit_code,
        diagnostics=diagnostics,
        receipt_id=receipt_id or f"vc-{safe_id(job_id or request.get('request_id'), 'job')}",
    )
    executor = receipt["executor"]
    executor["executor_id"] = safe_id(executor_id or f"sure-vc-{executor_kind}", f"sure-vc-{executor_kind}")
    executor["trust_level"] = executor_trust_level
    return receipt


def derive_execution_admission_trace(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    probe_invoked: bool = False,
    execute_invoked: bool | None = None,
    observed_at: str | None = None,
    forbidden_output_roots: Sequence[Path] = (),
) -> dict[str, Any]:
    """Derive a conservative admission trace for a legacy Python runner.

    Legacy runners do not expose Core's adapter callbacks.  A terminal or
    queued/running receipt therefore proves only that the launch boundary was
    attempted; capability evidence by itself is not treated as a probe.  A
    caller may explicitly override ``execute_invoked`` when it knows a spawn
    attempt happened before a ``NOT_STARTED`` receipt was built.
    """

    lifecycle = receipt.get("lifecycle")
    diagnostics = receipt.get("diagnostics") if isinstance(receipt.get("diagnostics"), list) else []
    codes = {
        str(item.get("code"))
        for item in diagnostics
        if isinstance(item, Mapping) and isinstance(item.get("code"), str)
    }
    if "CAPABILITY_MISSING" in codes:
        reason_code = "CAPABILITY_MISSING"
        inferred_execute = False
    elif lifecycle == "SUCCEEDED":
        reason_code = "VALIDATION_PENDING"
        inferred_execute = True
    elif lifecycle == "FAILED":
        reason_code = "EXECUTION_FAILED"
        inferred_execute = True
    elif lifecycle == "PARTIAL":
        reason_code = "EXECUTION_PARTIAL"
        inferred_execute = True
    elif lifecycle == "CANCELLED":
        reason_code = "EXECUTION_CANCELLED"
        inferred_execute = True
    elif lifecycle in {"QUEUED", "RUNNING"}:
        reason_code = "AWAITING_EXECUTION"
        inferred_execute = True
    elif "INVALID_CONTRACT" in codes:
        reason_code = "INVALID_CONTRACT"
        inferred_execute = False
    elif codes.intersection({"EXECUTOR_FAILED", "EXECUTOR_SPAWN_FAILED", "EXECUTOR_TIMEOUT"}):
        reason_code = "EXECUTION_FAILED"
        inferred_execute = True
    else:
        reason_code = "AWAITING_EXECUTION"
        inferred_execute = False
    if execute_invoked is None:
        execute_invoked = inferred_execute
    receipt_errors = validate_contract_pair(
        request,
        receipt,
        forbidden_output_roots=forbidden_output_roots,
    )
    return create_execution_admission_trace(
        request,
        observed_at=str(observed_at or receipt.get("finished_at") or receipt.get("started_at") or utc_now()),
        outcome_reason_code=reason_code,
        probe_invoked=probe_invoked,
        execute_invoked=execute_invoked,
        receipt=receipt,
        receipt_valid=not receipt_errors,
    )


def validate_contract_pair(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    forbidden_output_roots: Sequence[Path] = (),
) -> list[str]:
    """Perform the Python-side fail-closed checks shared by legacy gates."""

    errors: list[str] = []
    runtime_requirements = request.get("runtime_requirements")
    if isinstance(runtime_requirements, Mapping):
        errors.extend(validate_adapter_route(runtime_requirements))
    if request.get("schema") != REQUEST_SCHEMA:
        errors.append("execution request schema is unsupported")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        errors.append("execution receipt schema is unsupported")
    for field in ("request_id", "run_id", "unit_id", "semantic_request_digest", "reference_snapshot_digest", "policy_digest"):
        if not request.get(field):
            errors.append(f"request.{field} is missing")
    if receipt.get("request_id") != request.get("request_id"):
        errors.append("receipt.request_id does not match request")
    if receipt.get("run_id") != request.get("run_id"):
        errors.append("receipt.run_id does not match request")
    if receipt.get("unit_id") != request.get("unit_id"):
        errors.append("receipt.unit_id does not match request")
    if not same_digest(receipt.get("request_digest"), digest_json(request)):
        errors.append("receipt.request_digest does not match canonical request digest")
    if not same_digest(receipt.get("semantic_request_digest"), request.get("semantic_request_digest")):
        errors.append("receipt.semantic_request_digest does not match request")
    if not same_digest(receipt.get("reference_snapshot_digest"), request.get("reference_snapshot_digest")):
        errors.append("receipt.reference_snapshot_digest does not match request")
    if not same_digest(receipt.get("policy_digest"), request.get("policy_digest")):
        errors.append("receipt.policy_digest does not match request")
    surface = runtime_requirements.get("execution_surface") if isinstance(runtime_requirements, Mapping) else None
    if surface in EXECUTION_ADAPTER_SURFACES:
        if not valid_digest(request.get("policy_snapshot_digest")):
            errors.append("external execution requires request.policy_snapshot_digest")
        if not same_digest(receipt.get("policy_snapshot_digest"), request.get("policy_snapshot_digest")):
            errors.append("receipt.policy_snapshot_digest does not match request")
        if not valid_digest(request.get("adapter_manifest_digest")):
            errors.append("external execution requires request.adapter_manifest_digest")
        if not same_digest(receipt.get("adapter_manifest_digest"), request.get("adapter_manifest_digest")):
            errors.append("receipt.adapter_manifest_digest does not match request")
    elif receipt.get("policy_snapshot_digest") is not None:
        if not same_digest(receipt.get("policy_snapshot_digest"), request.get("policy_snapshot_digest")):
            errors.append("receipt.policy_snapshot_digest does not match request")
    if surface not in EXECUTION_ADAPTER_SURFACES:
        if request.get("adapter_manifest_digest") is not None:
            errors.append("request.adapter_manifest_digest is not allowed without an external execution surface")
        if receipt.get("adapter_manifest_digest") is not None:
            errors.append("receipt.adapter_manifest_digest is not allowed without an external execution surface")
    lifecycle = receipt.get("lifecycle")
    if lifecycle not in LIFECYCLES:
        errors.append(f"receipt.lifecycle is invalid: {lifecycle!r}")
    if lifecycle == "SUCCEEDED" and receipt.get("exit_code") != 0:
        errors.append("SUCCEEDED receipt must have exit_code 0")
    if lifecycle in TERMINAL_LIFECYCLES and not receipt.get("finished_at"):
        errors.append("terminal receipt must include finished_at")
    output_root = request.get("output_root") if isinstance(request.get("output_root"), Mapping) else {}
    root = Path(str(output_root.get("resolved_path") or "")).expanduser().resolve()
    for output in receipt.get("outputs") or []:
        if not isinstance(output, Mapping):
            errors.append("receipt output is not an object")
            continue
        candidate = Path(str(output.get("resolved_path") or "")).expanduser().resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"receipt output escapes output root: {candidate}")
        for forbidden in forbidden_output_roots:
            try:
                candidate.relative_to(forbidden.expanduser().resolve())
            except ValueError:
                continue
            errors.append(f"receipt output enters forbidden root: {candidate}")
    errors.extend(validate_output_binding(request, receipt))
    requirements = request.get("capability_requirements") if isinstance(request.get("capability_requirements"), list) else []
    raw_evidence = receipt.get("capability_evidence")
    errors.extend(validate_capability_evidence_list(raw_evidence, field="receipt.capability_evidence"))
    evidence = raw_evidence if isinstance(raw_evidence, list) else []
    capabilities = capability_summary(requirements, evidence)
    for key in ("missing", "unknown", "denied", "invalid"):
        errors.extend(f"required capability {item} is not admitted" for item in capabilities[key])
    return errors


def write_contract_bundle(
    artifacts_dir: Path,
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    admission_trace: Mapping[str, Any] | None = None,
    legacy_surface: Path | None = None,
    legacy_result: Path | None = None,
    forbidden_output_roots: Sequence[Path] = (),
) -> dict[str, Any]:
    """Persist request/receipt views and an immutable-by-id execution history.

    The fixed filenames are intentionally retained for legacy gates that read
    the latest execution.  A run can contain several trans validation units,
    however, so those aliases cannot be the only provenance record.  When
    supplied, ``admission_trace`` is persisted as a separate preflight record;
    it never substitutes for the receipt.
    """

    artifacts_dir = artifacts_dir.expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    request_path = artifacts_dir / "execution_request.json"
    receipt_path = artifacts_dir / "execution_receipt.json"
    admission_path = artifacts_dir / "execution_admission.json"
    request_id = safe_id(request.get("request_id"), "request")
    history_dir = artifacts_dir / "execution_contracts"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_request_path = history_dir / f"{request_id}.request.json"
    history_receipt_path = history_dir / f"{request_id}.receipt.json"
    history_admission_path = history_dir / f"{request_id}.admission.json"
    _write_immutable_json(history_request_path, request)
    _write_immutable_json(history_receipt_path, receipt)
    write_json(request_path, request)
    write_json(receipt_path, receipt)
    errors = validate_contract_pair(request, receipt, forbidden_output_roots=forbidden_output_roots)
    admission_errors: list[str] = []
    if admission_trace is not None:
        admission_errors = validate_execution_admission_receipt_binding(
            request,
            admission_trace,
            receipt=receipt,
            receipt_valid=not errors,
        )
        _write_immutable_json(history_admission_path, admission_trace)
        write_json(admission_path, admission_trace)
    contract = {
        "schema": COMPATIBILITY_SCHEMA,
        "version": 1,
        "request_path": str(request_path),
        "receipt_path": str(receipt_path),
        "request_digest": digest_json(dict(request)),
        "receipt_digest": digest_json(dict(receipt)),
        "lifecycle": receipt.get("lifecycle"),
        "admission_instrumentation": "admission-v1" if admission_trace is not None else "legacy-uninstrumented",
        "contract_valid": not errors and not admission_errors,
        "diagnostics": [*errors, *admission_errors],
        "legacy_views": {
            "execution_surface": str(legacy_surface) if legacy_surface else None,
            "execution_result": str(legacy_result) if legacy_result else None,
        },
        "history": {
            "request_path": str(history_request_path),
            "receipt_path": str(history_receipt_path),
            "admission_path": str(history_admission_path) if admission_trace is not None else None,
        },
    }
    if admission_trace is not None:
        contract["admission_path"] = str(admission_path)
        contract["admission_digest"] = digest_json(dict(admission_trace))
    write_json(artifacts_dir / "execution_contract.json", contract)
    history_contract = {
        **contract,
        "request_path": str(history_request_path),
        "receipt_path": str(history_receipt_path),
    }
    _write_immutable_json(history_dir / f"{request_id}.contract.json", history_contract)
    return contract


def contract_failure_diagnostics(error: BaseException, *, code: str) -> list[dict[str, Any]]:
    return [{"code": code, "message": str(error) or error.__class__.__name__}]
