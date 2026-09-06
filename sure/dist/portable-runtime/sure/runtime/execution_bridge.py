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
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REQUEST_SCHEMA = "sure.execution_request.v1"
RECEIPT_SCHEMA = "sure.execution_receipt.v1"
COMPATIBILITY_SCHEMA = "sure.execution_compatibility.v1"
DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TERMINAL_LIFECYCLES = {"SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED"}
LIFECYCLES = {"NOT_STARTED", "QUEUED", "RUNNING", *TERMINAL_LIFECYCLES}


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
    """Build a strict artifact reference for an existing regular file."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise FileNotFoundError(resolved)
    if origin not in {"local_staging", "read_only_reference", "generated", "external"}:
        raise ValueError(f"unsupported artifact origin: {origin}")
    result: dict[str, Any] = {
        "artifact_id": safe_id(artifact_id, f"artifact-{hashlib.sha256(str(resolved).encode()).hexdigest()[:12]}"),
        "path": str(path.expanduser().absolute()),
        "resolved_path": str(resolved),
        "sha256": digest_file(resolved),
        "size": resolved.stat().st_size,
        "media_type": "application/json" if resolved.suffix.lower() == ".json" else "application/octet-stream",
        "origin": origin,
        "source_root": str(_source_root(resolved, source_root)),
    }
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


def capability_summary(
    requirements: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]]
) -> dict[str, list[str] | bool]:
    by_id = {str(item.get("capability_id")): item for item in evidence}
    missing: list[str] = []
    unknown: list[str] = []
    denied: list[str] = []
    invalid: list[str] = []
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
    reference_snapshot_digest: str | None = None,
    request_id: str | None = None,
    created_at: str | None = None,
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
    if lifecycle in TERMINAL_LIFECYCLES:
        receipt["finished_at"] = finished_at or utc_now()
    if exit_code is not None:
        receipt["exit_code"] = int(exit_code)
    if diagnostics:
        receipt["diagnostics"] = [dict(item) for item in diagnostics]
    return receipt


def validate_contract_pair(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    forbidden_output_roots: Sequence[Path] = (),
) -> list[str]:
    """Perform the Python-side fail-closed checks shared by legacy gates."""

    errors: list[str] = []
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
    requirements = request.get("capability_requirements") if isinstance(request.get("capability_requirements"), list) else []
    evidence = receipt.get("capability_evidence") if isinstance(receipt.get("capability_evidence"), list) else []
    capabilities = capability_summary(requirements, evidence)
    for key in ("missing", "unknown", "denied", "invalid"):
        errors.extend(f"required capability {item} is not admitted" for item in capabilities[key])
    return errors


def write_contract_bundle(
    artifacts_dir: Path,
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    legacy_surface: Path | None = None,
    legacy_result: Path | None = None,
    forbidden_output_roots: Sequence[Path] = (),
) -> dict[str, Any]:
    """Persist request/receipt views and an immutable-by-id execution history.

    The fixed filenames are intentionally retained for legacy gates that read
    the latest execution.  A run can contain several trans validation units,
    however, so those aliases cannot be the only provenance record.
    """

    artifacts_dir = artifacts_dir.expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    request_path = artifacts_dir / "execution_request.json"
    receipt_path = artifacts_dir / "execution_receipt.json"
    request_id = safe_id(request.get("request_id"), "request")
    history_dir = artifacts_dir / "execution_contracts"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_request_path = history_dir / f"{request_id}.request.json"
    history_receipt_path = history_dir / f"{request_id}.receipt.json"
    _write_immutable_json(history_request_path, request)
    _write_immutable_json(history_receipt_path, receipt)
    write_json(request_path, request)
    write_json(receipt_path, receipt)
    errors = validate_contract_pair(request, receipt, forbidden_output_roots=forbidden_output_roots)
    contract = {
        "schema": COMPATIBILITY_SCHEMA,
        "version": 1,
        "request_path": str(request_path),
        "receipt_path": str(receipt_path),
        "request_digest": digest_json(dict(request)),
        "receipt_digest": digest_json(dict(receipt)),
        "lifecycle": receipt.get("lifecycle"),
        "contract_valid": not errors,
        "diagnostics": errors,
        "legacy_views": {
            "execution_surface": str(legacy_surface) if legacy_surface else None,
            "execution_result": str(legacy_result) if legacy_result else None,
        },
        "history": {
            "request_path": str(history_request_path),
            "receipt_path": str(history_receipt_path),
        },
    }
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
