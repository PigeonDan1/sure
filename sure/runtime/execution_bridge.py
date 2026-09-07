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
