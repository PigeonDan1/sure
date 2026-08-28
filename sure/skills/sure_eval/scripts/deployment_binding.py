#!/usr/bin/env python3
"""Validate and normalize the approved NFS container deployment contract."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy


class DeploymentBindingError(ValueError):
    """The approved model does not contain a usable immutable deployment."""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _artifact_path(model_dir: Path, relative: str) -> Path:
    path = (model_dir / relative).resolve()
    if not _is_relative_to(path, model_dir.resolve()):
        raise DeploymentBindingError(f"deployment artifact escapes approved model directory: {relative}")
    if not path.is_file():
        raise DeploymentBindingError(f"required deployment artifact is missing: {relative}")
    return path


def _read_json(model_dir: Path, relative: str) -> dict[str, Any]:
    path = _artifact_path(model_dir, relative)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentBindingError(f"invalid JSON deployment artifact {relative}: {exc}") from exc
    if not isinstance(value, dict):
        raise DeploymentBindingError(f"deployment artifact must be a JSON object: {relative}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repository(image: str) -> str:
    without_digest = image.split("@", 1)[0]
    slash = without_digest.rfind("/")
    colon = without_digest.rfind(":")
    return without_digest[:colon] if colon > slash else without_digest


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DeploymentBindingError(message)


def _verified_bundle_evidence(model_dir: Path, marker: dict[str, Any]) -> tuple[dict[str, str], str]:
    declared_hashes = marker.get("required_artifact_sha256")
    _require(isinstance(declared_hashes, dict) and bool(declared_hashes), "deployment artifact hashes are missing")
    verified_hashes: dict[str, str] = {}
    for relative, expected in declared_hashes.items():
        _require(isinstance(relative, str) and isinstance(expected, str), "deployment artifact hash entry is invalid")
        actual = _sha256(_artifact_path(model_dir, relative))
        _require(actual == expected, f"deployment artifact hash mismatch: {relative}")
        verified_hashes[relative] = actual
    bundle_identity = str(marker.get("bundle_identity_sha256") or "")
    calculated_bundle_identity = hashlib.sha256(
        json.dumps(declared_hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _require(bundle_identity == calculated_bundle_identity, "deployment bundle identity does not match required artifact hashes")
    return verified_hashes, bundle_identity


def _python_runtime_path(
    model_dir: Path,
    raw: object,
    scope: object,
    *,
    executable: bool = True,
) -> Path:
    _require(isinstance(raw, str) and bool(raw), "approved Model Python path is missing")
    path = Path(raw).expanduser()
    if scope == "model_relative":
        _require(not path.is_absolute(), "model_relative Python path must be relative")
        candidate = model_dir / path
        resolved = candidate.parent.resolve() / candidate.name if executable else candidate.resolve()
        _require(_is_relative_to(resolved, model_dir), "model_relative Python path escapes approved model")
    elif scope == "site_runtime":
        _require(path.is_absolute(), "site_runtime Python path must be absolute")
        configured = load_site_policy(required=True)
        runtime_root = Path(configured["policy"]["storage"]["runtime_root"]).expanduser().resolve()
        resolved = path.parent.resolve() / path.name if executable else path.resolve()
        _require(_is_relative_to(resolved, runtime_root), f"site_runtime Python path escapes {runtime_root}")
    else:
        raise DeploymentBindingError(f"unsupported Python runtime path_scope: {scope!r}")
    _require(resolved.is_file(), f"approved runtime path does not exist: {resolved}")
    if executable:
        _require(bool(resolved.stat().st_mode & 0o111), f"approved Model Python is not executable: {resolved}")
    return resolved


def _load_python_deployment_binding(
    model_dir: Path,
    model_name: str,
    marker: dict[str, Any],
    inventory: dict[str, Any],
    package: dict[str, Any],
) -> dict[str, Any]:
    site_policy = load_site_policy(required=True)["policy"]
    _require(
        "python" in site_policy["execution"]["local_runtimes"],
        "local Python Eval is disabled by site policy; add python to execution.local_runtimes",
    )
    _require(marker.get("schema") == "sure.onboard.deployment_ready.v1", "unsupported deployment_ready schema")
    _require(marker.get("status") == "ready", "deployment_ready status must be ready")
    _require(marker.get("model_name") == model_name, "deployment_ready model_name does not match requested model")
    _require(marker.get("package_profile") == "none", "approved Python model must use package=none")
    execution_policy = marker.get("execution_policy")
    _require(isinstance(execution_policy, dict), "deployment_ready execution_policy is missing")
    _require(execution_policy.get("container_only") is False, "Python deployment must not be container_only")
    _require(execution_policy.get("eval_runtime") == "python", "deployment is not approved for Python Eval")
    _require(execution_policy.get("nfs_models_read_only") is True, "approved model policy must remain read-only")
    _require(execution_policy.get("host_python_fallback") is False, "Python runtime must be explicit, not a fallback")

    _require(inventory.get("schema") == "sure.onboard.runtime_inventory.v2", "unsupported runtime_inventory schema")
    _require(inventory.get("status") == "ready", "runtime_inventory status must be ready")
    model = inventory.get("model")
    policy = inventory.get("policy")
    local = inventory.get("local_runtime")
    _require(isinstance(model, dict) and model.get("name") == model_name, "runtime_inventory model does not match")
    _require(isinstance(policy, dict) and policy.get("eval_runtime") == "python_only", "runtime inventory is not Python-ready")
    _require(policy.get("host_python_fallback") is False, "runtime inventory enables host fallback")
    _require(policy.get("nfs_models_mutable_by_eval") is False, "runtime inventory allows approved model mutation")
    _require(isinstance(local, dict) and local.get("eligible_for_eval") is True, "approved Python runtime is missing")
    _require(local.get("backend") in {"uv", "pip", "conda", "pixi"}, "approved Python backend is unsupported")
    python = _python_runtime_path(model_dir, local.get("python_executable"), local.get("path_scope"))

    lockfiles = local.get("lockfiles")
    lock_scopes = local.get("lockfile_scopes")
    lock_hashes = local.get("lockfile_sha256")
    _require(isinstance(lockfiles, list) and bool(lockfiles), "approved Python runtime has no lockfile")
    _require(isinstance(lock_scopes, dict) and isinstance(lock_hashes, dict), "approved Python lock evidence is missing")
    verified_locks: dict[str, str] = {}
    for lockfile in lockfiles:
        lock_path = _python_runtime_path(model_dir, lockfile, lock_scopes.get(lockfile), executable=False)
        actual = _sha256(lock_path)
        _require(lock_hashes.get(lockfile) == actual, f"approved Python lockfile hash mismatch: {lockfile}")
        verified_locks[str(lock_path)] = actual

    command = local.get("server_command")
    tools = local.get("tool_names")
    _require(isinstance(command, list) and bool(command) and all(isinstance(item, str) and item for item in command), "approved Python server_command is invalid")
    _require(command[0] == local.get("python_executable"), "approved Python server_command must use the bound interpreter")
    _require(isinstance(tools, list) and bool(tools) and all(isinstance(item, str) and item for item in tools), "approved Python tool_names are invalid")
    working_dir = (model_dir / str(local.get("working_dir") or ".")).resolve()
    _require(_is_relative_to(working_dir, model_dir) and working_dir.is_dir(), "approved Python working_dir is invalid")

    _require(package.get("schema") == "sure.onboard.package_gate.v2", "unsupported package_gate schema")
    _require(package.get("status") == "passed" and package.get("package_profile") == "none", "package_gate is not Python-ready")
    readiness = package.get("readiness")
    _require(isinstance(readiness, dict) and readiness.get("local_ready") is True, "package_gate local readiness is missing")
    verified_hashes, bundle_identity = _verified_bundle_evidence(model_dir, marker)
    return {
        "schema": "sure.eval.deployment_binding.v1",
        "runtime_kind": "python",
        "model_name": model_name,
        "model_dir": str(model_dir),
        "source": "approved_nfs_models",
        "package_profile": "none",
        "python": {
            "backend": local.get("backend"),
            "python_executable": str(python),
            "python_version": local.get("python_version"),
            "working_dir": str(working_dir),
            "server_command": [str(python), *command[1:]],
            "tool_names": tools,
            "required_imports": local.get("required_imports") if isinstance(local.get("required_imports"), list) else [],
            "gpu_required": local.get("gpu_required") is True,
            "lock_sha256": verified_locks,
        },
        "policy": {
            "execution_mode": "python",
            "host_python_fallback": False,
            "image_override_allowed": False,
            "nfs_models_read_only": True,
        },
        "evidence": {
            "deployment_ready": str(_artifact_path(model_dir, "artifacts/deployment_ready.json")),
            "runtime_inventory": str(_artifact_path(model_dir, "artifacts/runtime_inventory.json")),
            "package_gate": str(_artifact_path(model_dir, "artifacts/package_gate.json")),
            "verified_sha256": verified_hashes,
            "bundle_identity_sha256": bundle_identity,
            "runtime_lock_sha256": verified_locks,
        },
    }


def load_deployment_binding(model_dir: Path, model_name: str) -> dict[str, Any]:
    """Return the only execution binding that `/sure_eval` may consume."""

    model_dir = model_dir.expanduser().resolve()
    marker = _read_json(model_dir, "artifacts/deployment_ready.json")
    inventory = _read_json(model_dir, "artifacts/runtime_inventory.json")
    package = _read_json(model_dir, "artifacts/package_gate.json")

    if marker.get("package_profile") == "none":
        return _load_python_deployment_binding(model_dir, model_name, marker, inventory, package)

    _require(marker.get("schema") == "sure.onboard.deployment_ready.v1", "unsupported deployment_ready schema")
    _require(marker.get("status") == "ready", "deployment_ready status must be ready")
    _require(marker.get("model_name") == model_name, "deployment_ready model_name does not match requested model")
    _require(marker.get("package_profile") == "docker-registry", "approved local model must use docker-registry")

    execution_policy = marker.get("execution_policy")
    _require(isinstance(execution_policy, dict), "deployment_ready execution_policy is missing")
    _require(execution_policy.get("container_only") is True, "deployment must be container_only")
    _require(execution_policy.get("nfs_models_read_only") is True, "NFS model mount must be read-only")
    _require(execution_policy.get("host_python_fallback") is False, "host Python fallback must be disabled")
    _require(execution_policy.get("approved_image_override") is False, "image override must be disabled")

    _require(inventory.get("schema") == "sure.onboard.runtime_inventory.v2", "unsupported runtime_inventory schema")
    _require(inventory.get("status") == "ready", "runtime_inventory status must be ready")
    model = inventory.get("model")
    policy = inventory.get("policy")
    container = inventory.get("container_runtime")
    _require(isinstance(model, dict) and model.get("name") == model_name, "runtime_inventory model does not match")
    _require(isinstance(policy, dict) and policy.get("eval_runtime") == "container_only", "runtime inventory is not container-only")
    _require(policy.get("host_python_fallback") is False, "runtime inventory enables host fallback")
    _require(policy.get("image_override_allowed") is False, "runtime inventory enables image override")
    _require(policy.get("nfs_models_mutable_by_eval") is False, "runtime inventory allows NFS mutation")
    _require(isinstance(container, dict) and container.get("required") is True, "container runtime is missing")

    _require(package.get("schema") == "sure.onboard.package_gate.v2", "unsupported package_gate schema")
    _require(package.get("status") == "passed", "package_gate status must be passed")
    _require(package.get("package_profile") == "docker-registry", "package_gate must use docker-registry")
    _require(package.get("model_name", model_name) == model_name, "package_gate model does not match")
    readiness = package.get("readiness")
    _require(isinstance(readiness, dict), "package_gate readiness is missing")
    for key in ("local_ready", "docker_ready", "registry_ready", "bundle_ready"):
        _require(readiness.get(key) is True, f"package_gate readiness.{key} must be true")

    package_docker = package.get("docker")
    _require(isinstance(package_docker, dict), "package_gate docker binding is missing")
    image = str(container.get("target_image") or "")
    digest = str(container.get("target_image_digest") or "")
    image_ref = str(container.get("target_image_ref") or "")
    _require(bool(image), "container target_image is missing")
    _require(digest.startswith("sha256:") and len(digest) == 71, "container image digest is invalid")
    _require(image_ref == f"{_repository(image)}@{digest}", "container target_image_ref is not digest-pinned")
    for source_name, source in (("deployment_ready", marker), ("package_gate.docker", package_docker)):
        _require(source.get("target_image") == image, f"{source_name} target_image disagrees with runtime inventory")
        _require(source.get("target_image_digest") == digest, f"{source_name} digest disagrees with runtime inventory")
        _require(source.get("target_image_ref") == image_ref, f"{source_name} image ref disagrees with runtime inventory")

    mount_policy = container.get("mount_policy")
    _require(isinstance(mount_policy, dict), "container mount_policy is missing")
    _require(mount_policy.get("nfs_models_read_only") is True, "container NFS mount policy must be read-only")
    model_mount = mount_policy.get("model_bundle")
    result_mount = mount_policy.get("result_workspace")
    _require(isinstance(model_mount, dict) and model_mount.get("read_only") is True, "model bundle mount must be read-only")
    _require(isinstance(result_mount, dict) and result_mount.get("read_only") is False, "result workspace must be writable")
    model_target = str(model_mount.get("target") or "")
    result_target = str(result_mount.get("target") or "")
    _require(Path(model_target).is_absolute(), "model bundle container target must be absolute")
    _require(Path(result_target).is_absolute(), "result workspace container target must be absolute")

    server_command = container.get("server_command")
    tool_names = container.get("tool_names")
    working_dir = str(container.get("working_dir") or model_target)
    python_executable = str(container.get("python_executable") or "")
    _require(isinstance(server_command, list) and all(isinstance(item, str) and item for item in server_command), "container server_command is invalid")
    _require(isinstance(tool_names, list) and all(isinstance(item, str) and item for item in tool_names), "container tool_names are invalid")
    _require(Path(working_dir).is_absolute(), "container working_dir must be absolute")
    _require(bool(python_executable), "container python_executable is missing")
    image_harness = inventory.get("harness_runtime")
    if isinstance(image_harness, dict) and image_harness.get("required") is True:
        _require(
            image_harness.get("schema") == "sure.harness.runtime.binding.v1",
            "container Harness Runtime schema is invalid",
        )
        for key in ("runtime_id", "lock_sha256", "python_executable", "manifest_path", "runtime_root"):
            _require(bool(image_harness.get(key)), f"container Harness Runtime {key} is missing")
        _require(
            Path(str(image_harness["python_executable"])).is_absolute()
            and Path(str(image_harness["manifest_path"])).is_absolute()
            and Path(str(image_harness["runtime_root"])).is_absolute(),
            "container Harness Runtime paths must be absolute",
        )
        _require(
            image_harness.get("python_executable") != python_executable,
            "container Harness Python must differ from Model Python",
        )
        marker_harness = marker.get("harness_runtime")
        if isinstance(marker_harness, dict):
            for key in ("runtime_id", "lock_sha256", "python_executable", "manifest_path", "runtime_root"):
                _require(
                    marker_harness.get(key) == image_harness.get(key),
                    f"deployment_ready Harness Runtime {key} disagrees with runtime inventory",
                )
        normalized_harness: dict[str, Any] | None = {
            key: value
            for key, value in image_harness.items()
            if key not in {"required", "checks"}
        }
    else:
        normalized_harness = None

    verified_hashes, bundle_identity = _verified_bundle_evidence(model_dir, marker)

    return {
        "schema": "sure.eval.deployment_binding.v1",
        "runtime_kind": "container",
        "model_name": model_name,
        "model_dir": str(model_dir),
        "source": "approved_nfs_models",
        "package_profile": "docker-registry",
        "target_image": image,
        "target_image_digest": digest,
        "target_image_ref": image_ref,
        "container": {
            "python_executable": python_executable,
            "working_dir": working_dir,
            "server_command": server_command,
            "tool_names": tool_names,
            "gpu_required": container.get("gpu_required") is True,
            "harness_runtime": normalized_harness,
            "model_mount": {"source": str(model_dir), "target": model_target, "read_only": True},
            "result_mount": {"target": result_target, "read_only": False},
        },
        "policy": {
            "execution_mode": "container_only",
            "host_python_fallback": False,
            "image_override_allowed": False,
            "nfs_models_read_only": True,
        },
        "evidence": {
            "deployment_ready": str(_artifact_path(model_dir, "artifacts/deployment_ready.json")),
            "runtime_inventory": str(_artifact_path(model_dir, "artifacts/runtime_inventory.json")),
            "package_gate": str(_artifact_path(model_dir, "artifacts/package_gate.json")),
            "verified_sha256": verified_hashes,
            "bundle_identity_sha256": bundle_identity,
            "harness_runtime_source": "approved_image" if normalized_harness else "legacy_external_common_runtime",
        },
    }
