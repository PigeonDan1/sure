"""Host-neutral semantic backend registry and operation resolver.

The registry is generated from ``sure/canonical/shared/evaluation/registry.ts``.
This module intentionally performs only read-only resolution: an executor may
then launch the returned file, but it cannot substitute an unregistered path
or a tree whose digest differs from the pinned manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


class SemanticBackendResolutionError(FileNotFoundError):
    """A backend manifest, operation, or implementation is unavailable/invalid."""


def _record(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticBackendResolutionError(f"{field} must be a non-empty string")
    return value


def _relative(value: Any, field: str) -> str:
    text = _required_string(value, field).replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise SemanticBackendResolutionError(f"{field} must be a relative non-escaping path: {value!r}")
    return path.as_posix()


def _root_kind(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if value not in {"skill", "repository"}:
        raise SemanticBackendResolutionError(f"{field} must be skill or repository")
    return value


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _digest_value(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise SemanticBackendResolutionError(f"{field} is invalid")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise SemanticBackendResolutionError(f"{field} is invalid") from exc
    return value


def _tree_files(root: Path, prefix: str = "") -> list[str]:
    files: list[str] = []
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise SemanticBackendResolutionError(f"cannot enumerate semantic backend tree: {root}") from exc
    for entry in entries:
        if entry.name in {"__pycache__", "node_modules"}:
            continue
        relative = f"{prefix}/{entry.name}" if prefix else entry.name
        # Do not follow links while calculating a pinned backend tree.
        try:
            mode = entry.lstat().st_mode
        except OSError as exc:
            raise SemanticBackendResolutionError(f"cannot inspect semantic backend tree: {entry}") from exc
        if stat.S_ISLNK(mode):
            raise SemanticBackendResolutionError(f"semantic backend tree contains a symlink: {entry}")
        if stat.S_ISDIR(mode):
            files.extend(_tree_files(entry, relative))
        elif stat.S_ISREG(mode) and not entry.name.endswith((".pyc", ".js", ".d.ts", ".map")):
            files.append(relative)
    return files


def _tree_digest(root: Path) -> str:
    rows = "\n".join(f"{name}\0{_digest_file(root / name)}" for name in _tree_files(root))
    return f"sha256:{hashlib.sha256(rows.encode('utf-8')).hexdigest()}"


def _repository_root(package_dir: Path, environment: Mapping[str, str]) -> Path:
    explicit = str(environment.get("SURE_REPOSITORY_ROOT") or "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not root.is_dir():
            raise SemanticBackendResolutionError(f"repository root does not exist: {root}")
        return root
    current = package_dir.expanduser().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "sure" / "canonical").is_dir() and (candidate / "sure" / "skills").is_dir():
            return candidate
    raise SemanticBackendResolutionError("cannot discover the SURE repository root")


@dataclass(frozen=True)
class SemanticBackendOperation:
    operation_id: str
    description: str
    entrypoint: str
    consumer_skill_ids: tuple[str, ...]
    kind: str
    timeout_ms: int
    deterministic: bool
    requires_policy_snapshot: bool | None = None
    artifact_mode: str | None = None
    output_contract: dict[str, Any] | None = None
    capability_requirements: tuple[dict[str, Any], ...] | None = None
    canonical_resource_digest: str | None = None
    legacy_resource_digest: str | None = None


@dataclass(frozen=True)
class SemanticBackendBundle:
    bundle_id: str
    version: str
    description: str
    canonical_root: str
    legacy_root: str
    operations: tuple[SemanticBackendOperation, ...]
    canonical_root_kind: str | None = None
    legacy_root_kind: str | None = None
    integrity_root: str | None = None
    canonical_tree_digest: str | None = None
    legacy_tree_digest: str | None = None


@dataclass(frozen=True)
class SemanticBackendManifest:
    registry_digest: str
    bundles: tuple[SemanticBackendBundle, ...]


@dataclass(frozen=True)
class ResolvedSemanticBackend:
    operation_id: str
    bundle_id: str
    bundle_version: str
    path: Path
    bundle_root: Path
    integrity_root: str
    source: str
    resource_digest: str
    bundle_digest: str | None
    registry_digest: str
    timeout_ms: int
    deterministic: bool
    requires_policy_snapshot: bool
    artifact_mode: str | None = None
    output_contract: dict[str, Any] | None = None
    capability_requirements: tuple[dict[str, Any], ...] | None = None


def _validate_output_contract(value: Any, operation_id: str, kind: str, artifact_mode: str | None) -> dict[str, Any]:
    """Validate the operation's declarative output boundary before digest admission."""
    if not isinstance(value, dict):
        raise SemanticBackendResolutionError(f"{operation_id}.output_contract must be an object")
    if value.get("schema") != "sure.execution_output_contract.v1":
        raise SemanticBackendResolutionError(f"{operation_id}.output_contract schema is unsupported")
    mode = value.get("mode")
    if mode not in {"preexisting", "mutating", "producing"}:
        raise SemanticBackendResolutionError(f"{operation_id}.output_contract.mode is invalid")
    if kind != "execute":
        raise SemanticBackendResolutionError(f"{operation_id}.output_contract is only valid for execute operations")
    if artifact_mode is not None and mode != artifact_mode:
        raise SemanticBackendResolutionError(f"{operation_id}.output_contract.mode must match artifact_mode")
    outputs = value.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise SemanticBackendResolutionError(f"{operation_id}.output_contract.outputs must not be empty")
    ids: set[str] = set()
    paths: set[str] = set()
    for index, output in enumerate(outputs):
        if not isinstance(output, dict):
            raise SemanticBackendResolutionError(f"{operation_id}.output_contract.outputs[{index}] must be an object")
        artifact_id = output.get("artifact_id")
        path = output.get("path")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise SemanticBackendResolutionError(f"{operation_id}.output_contract.outputs[{index}].artifact_id is invalid")
        if artifact_id in ids:
            raise SemanticBackendResolutionError(f"{operation_id}.output_contract.outputs[{index}].artifact_id is duplicated")
        ids.add(artifact_id)
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
            raise SemanticBackendResolutionError(f"{operation_id}.output_contract.outputs[{index}].path is invalid")
        normalized = PurePosixPath(path).as_posix()
        if normalized != path or path in {".", ".."} or ".." in PurePosixPath(path).parts or "" in PurePosixPath(path).parts:
            raise SemanticBackendResolutionError(f"{operation_id}.output_contract.outputs[{index}].path is not normalized")
        if path in paths:
            raise SemanticBackendResolutionError(f"{operation_id}.output_contract.outputs[{index}].path is duplicated")
        paths.add(path)
        if output.get("kind") not in {"file", "directory"} or not isinstance(output.get("required"), bool):
            raise SemanticBackendResolutionError(f"{operation_id}.output_contract.outputs[{index}] has invalid kind/required")
    if mode == "producing" and not any(output.get("required") is True for output in outputs):
        raise SemanticBackendResolutionError(f"{operation_id}.output_contract must require an output for producing mode")
    temporary = value.get("temporary_paths")
    if not isinstance(temporary, list):
        raise SemanticBackendResolutionError(f"{operation_id}.output_contract.temporary_paths must be an array")
    temporary_paths: set[str] = set()
    for index, path in enumerate(temporary):
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
            raise SemanticBackendResolutionError(f"{operation_id}.output_contract.temporary_paths[{index}] is invalid")
        normalized = PurePosixPath(path).as_posix()
        if normalized != path or path in {".", ".."} or ".." in PurePosixPath(path).parts or "" in PurePosixPath(path).parts:
            raise SemanticBackendResolutionError(f"{operation_id}.output_contract.temporary_paths[{index}] is not normalized")
        if path in temporary_paths:
            raise SemanticBackendResolutionError(f"{operation_id}.output_contract.temporary_paths[{index}] is duplicated")
        temporary_paths.add(path)
    if not isinstance(value.get("allow_missing_on_failure"), bool) or not isinstance(value.get("retain_failed_outputs"), bool):
        raise SemanticBackendResolutionError(f"{operation_id}.output_contract failure policy is invalid")
    return value


def _validate_capability_requirements(value: Any, operation_id: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise SemanticBackendResolutionError(f"{operation_id}.capability_requirements must be an array")
    requirements: list[dict[str, Any]] = []
    for index, requirement in enumerate(value):
        if not isinstance(requirement, dict):
            raise SemanticBackendResolutionError(f"{operation_id}.capability_requirements[{index}] is invalid")
        capability_id = requirement.get("capability_id")
        if (
            not isinstance(capability_id, str)
            or re.fullmatch(r"sure\.[a-z0-9][a-z0-9.-]*", capability_id) is None
            or requirement.get("capability_class") != "execution_capability"
            or not isinstance(requirement.get("required"), bool)
        ):
            raise SemanticBackendResolutionError(f"{operation_id}.capability_requirements[{index}] is invalid")
        requirements.append(requirement)
    return tuple(requirements)


def _parse_manifest(value: Any, path: Path) -> SemanticBackendManifest:
    root = _record(value)
    if root is None or root.get("schema") not in {"sure.semantic.backend.manifest.v1", "sure.semantic.backends.v1"}:
        raise SemanticBackendResolutionError(f"unsupported semantic backend manifest: {path}")
    registry_digest = _required_string(root.get("registry_digest"), "registry_digest")
    if not registry_digest.startswith("sha256:") or len(registry_digest) != 71:
        raise SemanticBackendResolutionError("registry_digest is invalid")
    raw_bundles = root.get("bundles")
    if not isinstance(raw_bundles, list) or not raw_bundles:
        raise SemanticBackendResolutionError("backend manifest has no bundles")
    bundles: list[SemanticBackendBundle] = []
    bundle_ids: set[str] = set()
    operation_ids: set[str] = set()
    for bundle_index, raw_bundle in enumerate(raw_bundles):
        bundle = _record(raw_bundle)
        if bundle is None or bundle.get("schema") != "sure.semantic.backend.bundle.v1":
            raise SemanticBackendResolutionError(f"bundles[{bundle_index}] has an unsupported schema")
        raw_operations = bundle.get("operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise SemanticBackendResolutionError(f"bundles[{bundle_index}] has no operations")
        operations: list[SemanticBackendOperation] = []
        integrity_root = (
            _relative(bundle.get("integrity_root"), "integrity_root")
            if bundle.get("integrity_root") is not None
            else None
        )
        seen: set[str] = set()
        for operation_index, raw_operation in enumerate(raw_operations):
            operation = _record(raw_operation)
            if operation is None:
                raise SemanticBackendResolutionError(f"operations[{operation_index}] must be an object")
            operation_id = _required_string(operation.get("operation_id"), "operation_id")
            if operation_id in seen:
                raise SemanticBackendResolutionError(f"duplicate backend operation: {operation_id}")
            if operation_id in operation_ids:
                raise SemanticBackendResolutionError(f"duplicate backend operation: {operation_id}")
            seen.add(operation_id)
            operation_ids.add(operation_id)
            consumers = operation.get("consumer_skill_ids")
            if not isinstance(consumers, list) or any(not isinstance(item, str) for item in consumers):
                raise SemanticBackendResolutionError(f"{operation_id}.consumer_skill_ids must be a string array")
            kind = operation.get("kind")
            if kind not in {"execute", "validate", "resolve"}:
                raise SemanticBackendResolutionError(f"{operation_id}.kind is invalid")
            timeout = operation.get("timeout_ms")
            if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
                raise SemanticBackendResolutionError(f"{operation_id}.timeout_ms is invalid")
            if not isinstance(operation.get("deterministic"), bool):
                raise SemanticBackendResolutionError(f"{operation_id}.deterministic is invalid")
            requires_policy_snapshot = operation.get("requires_policy_snapshot")
            if requires_policy_snapshot is not None and not isinstance(requires_policy_snapshot, bool):
                raise SemanticBackendResolutionError(f"{operation_id}.requires_policy_snapshot is invalid")
            artifact_mode = operation.get("artifact_mode")
            if artifact_mode is not None and artifact_mode not in {"preexisting", "mutating", "producing"}:
                raise SemanticBackendResolutionError(f"{operation_id}.artifact_mode is invalid")
            if artifact_mode is not None and kind != "execute":
                raise SemanticBackendResolutionError(
                    f"{operation_id}.artifact_mode is only valid for execute operations"
                )
            output_contract = (
                _validate_output_contract(operation["output_contract"], operation_id, kind, artifact_mode)
                if "output_contract" in operation
                else None
            )
            capability_requirements = (
                _validate_capability_requirements(operation["capability_requirements"], operation_id)
                if "capability_requirements" in operation
                else None
            )
            entrypoint = _relative(operation.get("entrypoint"), f"{operation_id}.entrypoint")
            if integrity_root is not None:
                try:
                    PurePosixPath(entrypoint).relative_to(PurePosixPath(integrity_root))
                except ValueError as exc:
                    raise SemanticBackendResolutionError(
                        f"{operation_id}.entrypoint is outside integrity_root"
                    ) from exc
            digests: dict[str, str | None] = {
                field: _digest_value(operation.get(field), f"{operation_id}.{field}")
                for field in ("canonical_resource_digest", "legacy_resource_digest")
            }
            operations.append(
                SemanticBackendOperation(
                    operation_id=operation_id,
                    description=_required_string(operation.get("description"), f"{operation_id}.description"),
                    entrypoint=entrypoint,
                    consumer_skill_ids=tuple(consumers),
                    kind=kind,
                    timeout_ms=timeout,
                    deterministic=operation["deterministic"],
                    requires_policy_snapshot=requires_policy_snapshot,
                    artifact_mode=artifact_mode,
                    output_contract=output_contract,
                    capability_requirements=capability_requirements,
                    canonical_resource_digest=digests["canonical_resource_digest"],
                    legacy_resource_digest=digests["legacy_resource_digest"],
                )
            )
        bundles.append(
            SemanticBackendBundle(
                bundle_id=_required_string(bundle.get("bundle_id"), "bundle_id"),
                version=_required_string(bundle.get("version"), "version"),
                description=_required_string(bundle.get("description"), "description"),
                canonical_root=_relative(bundle.get("canonical_root"), "canonical_root"),
                legacy_root=_relative(bundle.get("legacy_root"), "legacy_root"),
                canonical_root_kind=_root_kind(bundle.get("canonical_root_kind"), "canonical_root_kind"),
                legacy_root_kind=_root_kind(bundle.get("legacy_root_kind"), "legacy_root_kind"),
                integrity_root=integrity_root,
                canonical_tree_digest=_digest_value(
                    bundle.get("canonical_tree_digest"),
                    f"bundles[{bundle_index}].canonical_tree_digest",
                ),
                legacy_tree_digest=_digest_value(
                    bundle.get("legacy_tree_digest"),
                    f"bundles[{bundle_index}].legacy_tree_digest",
                ),
                operations=tuple(operations),
            )
        )
        bundle_id = bundles[-1].bundle_id
        if bundle_id in bundle_ids:
            raise SemanticBackendResolutionError(f"duplicate backend bundle: {bundle_id}")
        bundle_ids.add(bundle_id)
    # The generated registry digest covers the canonical manifest schema even
    # when it is embedded in a host projection with an extra `schema` value.
    unsigned = {
        "schema": "sure.semantic.backend.manifest.v1",
        "bundles": [
            {
                "schema": "sure.semantic.backend.bundle.v1",
                "bundle_id": bundle.bundle_id,
                "version": bundle.version,
                "description": bundle.description,
                "canonical_root": bundle.canonical_root,
                **({"canonical_root_kind": bundle.canonical_root_kind} if bundle.canonical_root_kind is not None else {}),
                "legacy_root": bundle.legacy_root,
                **({"legacy_root_kind": bundle.legacy_root_kind} if bundle.legacy_root_kind is not None else {}),
                **({"integrity_root": bundle.integrity_root} if bundle.integrity_root is not None else {}),
                **({"canonical_tree_digest": bundle.canonical_tree_digest} if bundle.canonical_tree_digest is not None else {}),
                **({"legacy_tree_digest": bundle.legacy_tree_digest} if bundle.legacy_tree_digest is not None else {}),
                "operations": [
                    {
                        "operation_id": operation.operation_id,
                        "description": operation.description,
                        "entrypoint": operation.entrypoint,
                        "consumer_skill_ids": list(operation.consumer_skill_ids),
                        "kind": operation.kind,
                        "timeout_ms": operation.timeout_ms,
                        "deterministic": operation.deterministic,
                        **({"requires_policy_snapshot": operation.requires_policy_snapshot} if operation.requires_policy_snapshot is not None else {}),
                        **({"artifact_mode": operation.artifact_mode} if operation.artifact_mode is not None else {}),
                        **({"output_contract": operation.output_contract} if operation.output_contract is not None else {}),
                        **({"capability_requirements": list(operation.capability_requirements)} if operation.capability_requirements is not None else {}),
                        **({"canonical_resource_digest": operation.canonical_resource_digest} if operation.canonical_resource_digest is not None else {}),
                        **({"legacy_resource_digest": operation.legacy_resource_digest} if operation.legacy_resource_digest is not None else {}),
                    }
                    for operation in bundle.operations
                ],
            }
            for bundle in bundles
        ],
    }
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    computed = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    if computed != registry_digest:
        raise SemanticBackendResolutionError(f"semantic backend registry digest mismatch: {path}")
    return SemanticBackendManifest(registry_digest=registry_digest, bundles=tuple(bundles))


def load_semantic_backend_manifest(
    package_dir: str | Path | None = None,
    *,
    manifest_path: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> SemanticBackendManifest:
    env = os.environ if environment is None else environment
    package = Path(package_dir or env.get("SURE_SEMANTIC_BACKEND_PACKAGE_DIR") or Path.cwd()).expanduser().resolve()
    root = _repository_root(package, env)
    explicit = str(manifest_path or env.get("SURE_SEMANTIC_BACKEND_MANIFEST") or "").strip()
    candidates = [
        Path(explicit).expanduser().resolve() if explicit else None,
        package / "semantic-backends.json",
        root / "sure" / "canonical" / "shared" / "evaluation" / "backend-manifest.json",
    ]
    last_error: Exception | None = None
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        try:
            return _parse_manifest(json.loads(candidate.read_text(encoding="utf-8")), candidate)
        except Exception as exc:  # preserve the first invalid manifest as authoritative
            last_error = exc
            break
    if last_error is not None:
        if isinstance(last_error, SemanticBackendResolutionError):
            raise last_error
        raise SemanticBackendResolutionError(str(last_error)) from last_error
    raise SemanticBackendResolutionError("semantic backend manifest is not available")


def _contained_regular_file(path: Path, root: Path) -> bool:
    try:
        if not path.is_file() or path.is_symlink():
            return False
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def resolve_semantic_backend_operation(
    operation_id: str,
    *,
    package_dir: str | Path | None = None,
    manifest_path: str | Path | None = None,
    expected_registry_digest: str | None = None,
    expected_bundle_digest: str | None = None,
    verify_tree: bool = True,
    environment: Mapping[str, str] | None = None,
) -> ResolvedSemanticBackend:
    env = os.environ if environment is None else environment
    package = Path(package_dir or env.get("SURE_SEMANTIC_BACKEND_PACKAGE_DIR") or Path.cwd()).expanduser().resolve()
    manifest = load_semantic_backend_manifest(package, manifest_path=manifest_path, environment=env)
    if expected_registry_digest and manifest.registry_digest != expected_registry_digest:
        raise SemanticBackendResolutionError("semantic backend registry is not the expected digest")
    selected: tuple[SemanticBackendBundle, SemanticBackendOperation] | None = None
    for bundle in manifest.bundles:
        for operation in bundle.operations:
            if operation.operation_id == operation_id:
                selected = (bundle, operation)
                break
        if selected:
            break
    if selected is None:
        raise SemanticBackendResolutionError(f"semantic backend operation is not registered: {operation_id}")
    bundle, operation = selected
    integrity_root = bundle.integrity_root or "."
    root = _repository_root(package, env)
    candidates: list[tuple[Path, Path, str, str | None, str | None]] = []
    backend_root = str(env.get("SURE_SEMANTIC_BACKEND_ROOT") or "").strip()
    if backend_root:
        base = Path(backend_root).expanduser().resolve()
        candidates.extend(
            [
                (base / bundle.bundle_id / operation.entrypoint, base / bundle.bundle_id, "semantic-backend-root", operation.canonical_resource_digest, bundle.canonical_tree_digest),
                (base / operation.entrypoint, base, "semantic-backend-root", operation.canonical_resource_digest, bundle.canonical_tree_digest),
            ]
        )
    package_backend = package / "backends" / bundle.bundle_id
    candidates.append((package_backend / operation.entrypoint, package_backend, "package", operation.canonical_resource_digest, bundle.canonical_tree_digest))
    if bundle.canonical_root_kind == "repository":
        canonical_root = (root / bundle.canonical_root).resolve()
    else:
        canonical_skills_root = Path(
            str(env.get("SURE_CANONICAL_SKILLS_ROOT") or root / "sure" / "canonical" / "skills")
        ).expanduser().resolve()
        canonical_root = canonical_skills_root / bundle.canonical_root.removeprefix("skills/")
    candidates.append((canonical_root / operation.entrypoint, canonical_root, "canonical", operation.canonical_resource_digest, bundle.canonical_tree_digest))
    if bundle.legacy_root_kind == "repository":
        legacy_root = (root / bundle.legacy_root).resolve()
    else:
        legacy_skills_root = Path(
            str(env.get("SURE_LEGACY_SKILLS_ROOT") or root / "sure" / "skills")
        ).expanduser().resolve()
        legacy_root = legacy_skills_root / bundle.legacy_root.removeprefix("skills/")
    candidates.append((legacy_root / operation.entrypoint, legacy_root, "legacy", operation.legacy_resource_digest, bundle.legacy_tree_digest))
    for path, candidate_root, source, resource_digest, bundle_digest in candidates:
        try:
            path.lstat()
        except OSError:
            continue
        if not _contained_regular_file(path, candidate_root):
            raise SemanticBackendResolutionError(f"semantic backend entrypoint is not a contained regular file: {path}")
        if resource_digest and _digest_file(path) != resource_digest:
            raise SemanticBackendResolutionError(f"semantic backend entrypoint digest mismatch: {operation_id}")
        if expected_bundle_digest and bundle_digest and expected_bundle_digest != bundle_digest:
            raise SemanticBackendResolutionError(f"semantic backend bundle digest mismatch: {operation_id}")
        if verify_tree and bundle_digest and _tree_digest(candidate_root / integrity_root) != bundle_digest:
            raise SemanticBackendResolutionError(f"semantic backend tree digest mismatch: {operation_id}")
        return ResolvedSemanticBackend(
            operation_id=operation.operation_id,
            bundle_id=bundle.bundle_id,
            bundle_version=bundle.version,
            path=path,
            bundle_root=candidate_root,
            integrity_root=integrity_root,
            source=source,
            resource_digest=resource_digest or _digest_file(path),
            bundle_digest=bundle_digest,
            registry_digest=manifest.registry_digest,
            timeout_ms=operation.timeout_ms,
            deterministic=operation.deterministic,
            requires_policy_snapshot=operation.requires_policy_snapshot is True,
            artifact_mode=operation.artifact_mode,
            output_contract=operation.output_contract,
            capability_requirements=operation.capability_requirements,
        )
    raise SemanticBackendResolutionError(f"semantic backend operation is unavailable: {operation_id}")
