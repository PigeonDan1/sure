#!/usr/bin/env python3
"""Validate and normalize an approved model deployment contract."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from sure.runtime.model.bootstrap import ModelRuntimeError, manifest_sha256, verify_runtime
from sure.site.loader import SitePolicyError, load_site_policy


DEPLOYMENT_READY_V1 = "sure.onboard.deployment_ready.v1"
DEPLOYMENT_READY_V2 = "sure.onboard.deployment_ready.v2"
DEPLOYMENT_BINDING_V1 = "sure.eval.deployment_binding.v1"
DEPLOYMENT_BINDING_V2 = "sure.eval.deployment_binding.v2"


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
    declared = marker.get("required_artifact_sha256")
    _require(isinstance(declared, dict) and bool(declared), "deployment artifact hashes are missing")
    verified: dict[str, str] = {}
    for relative, expected in declared.items():
        _require(isinstance(relative, str) and isinstance(expected, str), "deployment artifact hash entry is invalid")
        actual = _sha256(_artifact_path(model_dir, relative))
        _require(actual == expected, f"deployment artifact hash mismatch: {relative}")
        verified[relative] = actual
    bundle_identity = str(marker.get("bundle_identity_sha256") or "")
    calculated = hashlib.sha256(
        json.dumps(declared, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _require(bundle_identity == calculated, "deployment bundle identity does not match required artifact hashes")
    return verified, bundle_identity


def _verified_model_core(model_dir: Path, inventory: dict[str, Any]) -> dict[str, str]:
    evidence = inventory.get("evidence") if isinstance(inventory.get("evidence"), dict) else {}
    declared = evidence.get("model_core_sha256")
    _require(isinstance(declared, dict) and bool(declared), "Python deployment model integrity hashes are missing")
    verified: dict[str, str] = {}
    for relative, expected in declared.items():
        _require(isinstance(relative, str) and isinstance(expected, str), "model integrity hash entry is invalid")
        actual = _sha256(_artifact_path(model_dir, relative))
        _require(actual == expected, f"approved model integrity hash mismatch: {relative}")
        verified[relative] = actual
    return verified


def _load_python_binding(
    model_dir: Path,
    model_name: str,
    marker: dict[str, Any],
    inventory: dict[str, Any],
    package: dict[str, Any],
) -> dict[str, Any]:
    try:
        configured = load_site_policy(required=True)
    except SitePolicyError as exc:
        raise DeploymentBindingError(str(exc)) from exc
    assert configured is not None
    site = configured["policy"]
    _require("python" in site["execution"]["local_runtimes"], "local Python Eval is disabled by site policy")
    _require("local" in site["execution"]["surfaces"], "local execution is disabled by site policy")
    _require(marker.get("status") == "ready", "deployment_ready status must be ready")
    _require(marker.get("model_name") == model_name, "deployment_ready model_name does not match requested model")
    policy = marker.get("execution_policy")
    _require(isinstance(policy, dict), "deployment_ready execution_policy is missing")
    expected_policy = {
        "container_only": False,
        "eval_runtime": "python",
        "isolation": "trusted_host",
        "model_integrity": "verify_before_after",
        "nfs_models_read_only": False,
        "model_bundle_mutation_allowed": False,
        "host_python_fallback": False,
        "approved_image_override": False,
    }
    for key, expected in expected_policy.items():
        _require(policy.get(key) == expected, f"Python deployment policy {key} must be {expected!r}")

    _require(inventory.get("schema") == "sure.onboard.runtime_inventory.v2", "unsupported runtime_inventory schema")
    _require(inventory.get("status") == "ready", "runtime_inventory status must be ready")
    model = inventory.get("model") if isinstance(inventory.get("model"), dict) else {}
    inventory_policy = inventory.get("policy") if isinstance(inventory.get("policy"), dict) else {}
    runtime = inventory.get("model_runtime") if isinstance(inventory.get("model_runtime"), dict) else {}
    _require(model.get("name") == model_name, "runtime_inventory model does not match")
    _require(inventory_policy.get("eval_runtime") == "python", "runtime inventory is not Python-ready")
    _require(inventory_policy.get("host_python_fallback") is False, "runtime inventory enables host fallback")
    _require(inventory_policy.get("nfs_models_mutable_by_eval") is False, "runtime inventory allows model mutation")
    _require(runtime.get("required") is True and runtime.get("backend") == "uv", "approved Model Runtime must be sealed by uv")

    _require(package.get("schema") == "sure.onboard.package_gate.v2", "unsupported package_gate schema")
    _require(package.get("status") == "passed" and package.get("package_profile") == "none", "package_gate is not Python-ready")
    readiness = package.get("readiness") if isinstance(package.get("readiness"), dict) else {}
    _require(readiness.get("local_ready") is True and readiness.get("bundle_ready") is True, "Python package readiness is incomplete")
    for key in ("container_ready", "docker_ready", "registry_ready"):
        _require(readiness.get(key) is False, f"Python package readiness.{key} must be false")

    manifest = _read_json(model_dir, "artifacts/model_runtime_manifest.json")
    marker_runtime = marker.get("model_runtime") if isinstance(marker.get("model_runtime"), dict) else {}
    _require(runtime.get("runtime_id") == manifest.get("runtime_id"), "runtime inventory Model Runtime ID mismatch")
    _require(marker_runtime.get("runtime_id") == manifest.get("runtime_id"), "deployment Model Runtime ID mismatch")
    expected_manifest_hash = manifest_sha256(manifest)
    _require(runtime.get("manifest_sha256") == expected_manifest_hash, "runtime inventory manifest hash mismatch")
    _require(marker_runtime.get("manifest_sha256") == expected_manifest_hash, "deployment manifest hash mismatch")
    lock_path = _artifact_path(model_dir, str(runtime.get("lockfile_path") or ""))
    _require(_sha256(lock_path) == manifest.get("lock_sha256") == runtime.get("lock_sha256"), "Model Runtime lock hash mismatch")
    try:
        resolved_runtime = verify_runtime(Path(site["storage"]["runtime_root"]) / "models", manifest)
    except ModelRuntimeError as exc:
        raise DeploymentBindingError(str(exc)) from exc

    command = runtime.get("server_command")
    tools = runtime.get("tool_names")
    relative_python = str(runtime.get("python_executable") or "")
    _require(
        isinstance(command, list)
        and bool(command)
        and all(isinstance(item, str) and item for item in command)
        and command[0] == relative_python,
        "approved Python server_command is invalid",
    )
    _require(isinstance(tools, list) and bool(tools) and all(isinstance(item, str) and item for item in tools), "approved Python tool_names are invalid")
    working_dir = (model_dir / str(runtime.get("working_dir") or ".")).resolve()
    _require(_is_relative_to(working_dir, model_dir) and working_dir.is_dir(), "approved Python working_dir is invalid")
    verified_hashes, bundle_identity = _verified_bundle_evidence(model_dir, marker)
    model_hashes = _verified_model_core(model_dir, inventory)
    python_executable = str(resolved_runtime["python_executable_resolved"])
    return {
        "schema": DEPLOYMENT_BINDING_V2,
        "runtime_kind": "python",
        "model_name": model_name,
        "model_dir": str(model_dir),
        "source": "approved_nfs_models",
        "package_profile": "none",
        "python": {
            "runtime_id": manifest["runtime_id"],
            "backend": "uv",
            "python_executable": python_executable,
            "python_version": manifest["python_version"],
            "python_abi": manifest["python_abi"],
            "runtime_root": resolved_runtime["runtime_root"],
            "manifest_path": resolved_runtime["manifest_path"],
            "manifest_sha256": expected_manifest_hash,
            "lockfile_path": str(lock_path),
            "lock_sha256": manifest["lock_sha256"],
            "working_dir": str(working_dir),
            "server_command": [python_executable, *command[1:]],
            "tool_names": tools,
            "required_imports": runtime.get("required_imports") or [],
            "gpu_required": runtime.get("gpu_required") is True,
        },
        "policy": {
            "execution_mode": "python",
            "isolation": "trusted_host",
            "model_integrity": "verify_before_after",
            "model_bundle_mutation_allowed": False,
            "host_python_fallback": False,
            "image_override_allowed": False,
        },
        "evidence": {
            "deployment_ready": str(_artifact_path(model_dir, "artifacts/deployment_ready.json")),
            "runtime_inventory": str(_artifact_path(model_dir, "artifacts/runtime_inventory.json")),
            "package_gate": str(_artifact_path(model_dir, "artifacts/package_gate.json")),
            "model_runtime_manifest": str(_artifact_path(model_dir, "artifacts/model_runtime_manifest.json")),
            "verified_sha256": verified_hashes,
            "model_core_sha256": model_hashes,
            "bundle_identity_sha256": bundle_identity,
        },
    }


def _is_sensitive_config_key(key: str) -> bool:
    upper = key.upper()
    if upper in {"API_KEY_ENV", "KEY_ENV", "CREDENTIAL_ENV"}:
        return False
    return any(part in upper for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY", "PRIVATE_KEY"))


def _read_yaml(model_dir: Path, relative: str) -> dict[str, Any]:
    path = _artifact_path(model_dir, relative)
    try:
        import yaml

        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise DeploymentBindingError(f"invalid YAML deployment artifact {relative}: {exc}") from exc
    if not isinstance(value, dict):
        raise DeploymentBindingError(f"deployment artifact must be a YAML object: {relative}")
    return value


def _api_config(model_dir: Path) -> dict[str, Any]:
    config = _read_yaml(model_dir, "config.yaml")
    api = config.get("api")
    if not isinstance(api, dict):
        raise DeploymentBindingError("API deployment config.yaml must contain an api mapping")
    leaked_keys = sorted(str(key) for key in api if _is_sensitive_config_key(str(key)))
    if leaked_keys:
        raise DeploymentBindingError(
            "API deployment config.yaml must not store secret values; "
            f"use api_key_env instead of {', '.join(leaked_keys)}"
        )
    api_key_env = str(api.get("api_key_env") or "").strip()
    model_id = str(api.get("model_id") or "").strip()
    endpoint = str(api.get("endpoint") or "").strip()
    base_url = str(api.get("base_url") or "").strip()
    if not api_key_env:
        raise DeploymentBindingError("API deployment api.api_key_env is required")
    if not model_id:
        raise DeploymentBindingError("API deployment api.model_id is required")
    if not endpoint and not base_url:
        raise DeploymentBindingError("API deployment requires api.endpoint or api.base_url")
    tools = config.get("tools") if isinstance(config.get("tools"), list) else []
    tool_names = [
        str(tool.get("name"))
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool.get("name")
    ]
    if not tool_names:
        tool_names = ["transcribe_audio"]
    return {
        "provider": str(api.get("provider") or "api"),
        "base_url": base_url or None,
        "endpoint": endpoint or None,
        "model_id": model_id,
        "api_key_env": api_key_env,
        "default_language": api.get("default_language"),
        "timeout": api.get("timeout"),
        "wrapper_module": str(api.get("wrapper_module") or "model"),
        "wrapper_class": str(api.get("wrapper_class") or ""),
        "predict_method": str(api.get("predict_method") or "predict"),
        "tool_names": tool_names,
    }


def _load_api_deployment_binding(
    model_dir: Path,
    model_name: str,
    marker: dict[str, Any],
    inventory: dict[str, Any],
    package: dict[str, Any],
) -> dict[str, Any]:
    _require(marker.get("status") == "api_ready", "API deployment_ready status must be api_ready")
    _require(marker.get("model_name") == model_name, "deployment_ready model_name does not match requested model")
    _require(marker.get("package_profile") == "none", "approved API model must use package_profile=none")

    execution_policy = marker.get("execution_policy")
    _require(isinstance(execution_policy, dict), "deployment_ready execution_policy is missing")
    _require(execution_policy.get("container_only") is False, "API deployment must not claim container_only")
    _require(execution_policy.get("nfs_models_read_only") is True, "NFS model mount must be read-only")
    _require(execution_policy.get("host_python_fallback") is False, "host Python fallback must be disabled")
    _require(execution_policy.get("approved_image_override") is False, "image override must be disabled")

    _require(inventory.get("schema") == "sure.onboard.runtime_inventory.v2", "unsupported runtime_inventory schema")
    _require(inventory.get("status") == "api_ready", "runtime_inventory status must be api_ready")
    model = inventory.get("model")
    policy = inventory.get("policy")
    container = inventory.get("container_runtime")
    model_runtime = inventory.get("model_runtime")
    _require(isinstance(model, dict) and model.get("name") == model_name, "runtime_inventory model does not match")
    _require(model.get("deployment_type") == "api", "runtime_inventory model.deployment_type must be api")
    _require(isinstance(policy, dict) and policy.get("eval_runtime") == "api_only", "runtime inventory is not API-only")
    _require(policy.get("host_python_fallback") is False, "runtime inventory enables host fallback")
    _require(policy.get("image_override_allowed") is False, "runtime inventory enables image override")
    _require(policy.get("nfs_models_mutable_by_eval") is False, "runtime inventory allows NFS mutation")
    _require(isinstance(container, dict) and container.get("required") is False, "API deployment must not require a container runtime")
    _require(isinstance(model_runtime, dict) and model_runtime.get("runtime_type") == "api", "model runtime must be api")

    _require(package.get("schema") == "sure.onboard.package_gate.v2", "unsupported package_gate schema")
    _require(package.get("status") == "passed", "package_gate status must be passed")
    _require(package.get("package_profile") == "none", "API package_gate must use package_profile=none")
    _require(package.get("model_name", model_name) == model_name, "package_gate model does not match")
    readiness = package.get("readiness")
    _require(isinstance(readiness, dict), "package_gate readiness is missing")
    _require(readiness.get("local_ready") is True, "API package_gate readiness.local_ready must be true")
    _require(readiness.get("bundle_ready") is True, "API package_gate readiness.bundle_ready must be true")

    verified_hashes, bundle_identity = _verified_bundle_evidence(model_dir, marker)
    _require("config.yaml" in verified_hashes, "API deployment_ready must hash config.yaml")
    api = _api_config(model_dir)
    if not api["wrapper_class"]:
        raise DeploymentBindingError("API deployment api.wrapper_class is required")

    return {
        "schema": DEPLOYMENT_BINDING_V1,
        "runtime_kind": "api",
        "model_name": model_name,
        "model_dir": str(model_dir),
        "source": "approved_nfs_models",
        "package_profile": "none",
        "target_image": None,
        "target_image_digest": None,
        "target_image_ref": None,
        "api": api,
        "policy": {
            "execution_mode": "api_only",
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
            "api_config_path": str(_artifact_path(model_dir, "config.yaml")),
            "secret_policy": "API secret values are read only from the declared api_key_env at runtime.",
        },
    }


def _normalize_harness_runtime(binding: dict[str, Any]) -> dict[str, Any]:
    """Accept legacy bindings by deriving root from their manifest location."""
    manifest_value = str(binding.get("manifest_path") or "")
    python_value = str(binding.get("python_executable") or "")
    _require(Path(manifest_value).is_absolute(), "container Harness Runtime manifest_path must be absolute")
    _require(Path(python_value).is_absolute(), "container Harness Runtime python_executable must be absolute")

    root_value = str(binding.get("runtime_root") or "")
    root = Path(root_value) if root_value else Path(manifest_value).parent
    _require(root.is_absolute(), "container Harness Runtime runtime_root must be absolute")
    _require(Path(manifest_value).parent == root, "container Harness Runtime manifest_path disagrees with runtime_root")
    try:
        Path(python_value).relative_to(root)
    except ValueError as exc:
        raise DeploymentBindingError("container Harness Runtime python_executable escapes runtime_root") from exc
    normalized = dict(binding)
    normalized["runtime_root"] = str(root)
    return normalized


TERMINAL_VERDICT_STATUSES = {"success", "passed", "pass"}


def _verify_artifact_hashes(model_dir: Path, marker: dict[str, Any]) -> tuple[dict[str, str], str]:
    declared_hashes = marker.get("required_artifact_sha256")
    _require(isinstance(declared_hashes, dict) and declared_hashes, "deployment_ready artifact hashes are missing")
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


def load_deployment_binding(model_dir: Path, model_name: str) -> dict[str, Any]:
    """Return the only execution binding that `/sure_eval` may consume."""

    model_dir = model_dir.expanduser().resolve()

    # A sealed bundle is supposed to carry a passing verdict; nothing here used to
    # look at it, so a bundle sitting next to a failed verdict resolved as usable.
    # Bundles that predate the verdict sidecar have none, so absence stays allowed.
    verdict_path = model_dir / "artifacts" / "verdict.json"
    if verdict_path.is_file():
        try:
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise DeploymentBindingError(f"verdict.json is not readable JSON: {error}") from error
        _require(
            isinstance(verdict, dict) and str(verdict.get("status", "")).lower() in TERMINAL_VERDICT_STATUSES,
            "verdict must be terminal-success for the bundle to be usable",
        )

    marker = _read_json(model_dir, "artifacts/deployment_ready.json")
    inventory = _read_json(model_dir, "artifacts/runtime_inventory.json")
    package = _read_json(model_dir, "artifacts/package_gate.json")

    if marker.get("status") == "api_ready":
        _require(marker.get("schema") == DEPLOYMENT_READY_V1, "unsupported API deployment_ready schema")
        return _load_api_deployment_binding(model_dir, model_name, marker, inventory, package)

    if marker.get("package_profile") == "none":
        _require(marker.get("schema") == DEPLOYMENT_READY_V2, "unsupported Python deployment_ready schema")
        return _load_python_binding(model_dir, model_name, marker, inventory, package)

    _require(marker.get("schema") == DEPLOYMENT_READY_V1, "unsupported deployment_ready schema")
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
        image_harness = _normalize_harness_runtime(image_harness)
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
            marker_harness = _normalize_harness_runtime(marker_harness)
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

    verified_hashes, bundle_identity = _verify_artifact_hashes(model_dir, marker)

    return {
        "schema": DEPLOYMENT_BINDING_V2,
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
