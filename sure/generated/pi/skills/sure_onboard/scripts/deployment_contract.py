"""Shared validation helpers for the onboard container delivery contract."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z0-9.-]+(?::[0-9]+)?/.+:[A-Za-z0-9_.-]+$")


def document_timestamp(document: dict[str, Any], name: str) -> datetime:
    raw = document.get("generated_at") or document.get("timestamp")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} must declare generated_at or timestamp")
    normalized = raw.strip().replace("Z", "+00:00")
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} has an invalid timestamp: {raw!r}") from exc
    if value.tzinfo is None:
        raise ValueError(f"{name} timestamp must include a timezone: {raw!r}")
    return value.astimezone(timezone.utc)


def timestamp_after(*dependencies: tuple[str, dict[str, Any]]) -> str:
    latest = max(document_timestamp(document, name) for name, document in dependencies)
    candidate = datetime.now(timezone.utc)
    if candidate <= latest:
        candidate = latest + timedelta(microseconds=1)
    return candidate.isoformat()


def require_timestamp_after(
    later_name: str,
    later: dict[str, Any],
    *dependencies: tuple[str, dict[str, Any]],
) -> None:
    later_time = document_timestamp(later, later_name)
    latest_name, latest_document = max(
        dependencies,
        key=lambda item: document_timestamp(item[1], item[0]),
    )
    latest_time = document_timestamp(latest_document, latest_name)
    if later_time <= latest_time:
        raise ValueError(
            f"{later_name} timestamp must be later than {latest_name}: "
            f"{later_time.isoformat()} <= {latest_time.isoformat()}"
        )


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def image_repository(image: str) -> str:
    repository, separator, tag = image.rpartition(":")
    if not separator or not tag or "/" not in repository:
        raise ValueError(f"target_image must include an explicit tag: {image!r}")
    return repository


def immutable_image_ref(image: str, digest: str) -> str:
    return f"{image_repository(image)}@{digest}"


def validate_image_and_digest(image: object, digest: object) -> tuple[str, str, str]:
    if not isinstance(image, str) or not IMAGE_TAG_RE.fullmatch(image):
        raise ValueError(f"invalid target_image: {image!r}")
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        raise ValueError(f"invalid target_image_digest: {digest!r}")
    return image, digest, immutable_image_ref(image, digest)


def load_artifact(run_dir: Path, model_dir: Path, name: str, *, required: bool = True) -> dict[str, Any]:
    candidates = (run_dir / "artifacts" / name, model_dir / "artifacts" / name)
    for path in candidates:
        if path.is_file():
            return read_json(path)
    if required:
        raise ValueError(f"missing required artifact: {name}")
    return {}


def load_model_artifact(model_dir: Path, name: str) -> dict[str, Any]:
    path = model_dir / "artifacts" / name
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing or unsafe model artifact: {name}")
    return read_json(path)


def resolve_model_dir(run_dir: Path) -> tuple[Path, dict[str, Any]]:
    artifacts = run_dir / "artifacts"
    candidates = (
        artifacts / "model_input_resolved.json",
        artifacts / "trans_input_resolved.json",
    )
    source = next((candidate for candidate in candidates if candidate.is_file()), None)
    if source is None:
        raise ValueError(
            "missing model_input_resolved.json or trans_input_resolved.json"
        )
    resolved = read_json(source)
    raw = resolved.get("model_dir")
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{source.name} must declare model_dir")
    model_dir = Path(raw).expanduser()
    if not model_dir.is_absolute():
        model_dir = Path.cwd() / model_dir
    return model_dir.resolve(), resolved


def resolve_evidence_path(raw: object, run_dir: Path, model_dir: Path) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path if path.exists() else None
    candidates = (
        run_dir / "artifacts" / path,
        run_dir / path,
        model_dir / path,
        model_dir / "artifacts" / path,
    )
    return next((candidate for candidate in candidates if candidate.exists()), None)


def passed(value: object) -> bool:
    return value is True or str(value).strip().lower() in {"pass", "passed", "success", "succeeded", "ready"}


def normalize_harness_runtime(
    harness: dict[str, Any],
    *,
    allow_derive: bool = True,
) -> dict[str, Any]:
    """Normalize the image Harness Runtime binding without site-specific paths."""
    manifest_value = str(harness.get("manifest_path") or "")
    python_value = str(harness.get("python_executable") or "")
    if not Path(manifest_value).is_absolute() or not Path(python_value).is_absolute():
        raise ValueError("image Harness Runtime paths must be absolute")

    root_value = str(harness.get("runtime_root") or "")
    if not root_value:
        if not allow_derive:
            raise ValueError("image Harness Runtime runtime_root is missing")
        root_value = str(Path(manifest_value).parent)
    root = Path(root_value)
    manifest = Path(manifest_value)
    python = Path(python_value)
    if not root.is_absolute():
        raise ValueError("image Harness Runtime runtime_root must be absolute")
    if manifest.parent != root:
        raise ValueError("image Harness Runtime manifest_path must be directly under runtime_root")
    try:
        python.relative_to(root)
    except ValueError as exc:
        raise ValueError("image Harness Runtime python_executable must be under runtime_root") from exc
    normalized = dict(harness)
    normalized["runtime_root"] = str(root)
    return normalized


def validate_runtime_roles(
    validation: dict[str, Any],
    *,
    expected_runtime_id: str | None = None,
    expected_lock_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    model = validation.get("model_runtime")
    harness = validation.get("harness_runtime")
    separation = validation.get("runtime_separation")
    if not isinstance(model, dict) or model.get("runtime_type") != "model_python":
        raise ValueError("docker_validation.json must declare model_runtime")
    if not isinstance(harness, dict) or harness.get("schema") != "sure.harness.runtime.binding.v1":
        raise ValueError("docker_validation.json must declare the common harness_runtime binding")
    if not isinstance(separation, dict) or separation.get("distinct_executables") is not True:
        raise ValueError("docker_validation.json must prove distinct Harness and Model Python roles")
    model_python = str(model.get("python_executable") or "")
    harness_python = str(harness.get("python_executable") or "")
    if not model_python or not harness_python or model_python == harness_python:
        raise ValueError("Harness Python and Model Python must be non-empty and distinct")
    harness = normalize_harness_runtime(harness)
    harness_python = str(harness.get("python_executable") or "")
    if not str(harness.get("runtime_id") or "") or not str(harness.get("lock_sha256") or ""):
        raise ValueError("image Harness Runtime identity and lock hash are required")
    if expected_runtime_id and harness.get("runtime_id") != expected_runtime_id:
        raise ValueError("image Harness Runtime ID differs from the active common runtime")
    if expected_lock_sha256 and harness.get("lock_sha256") != expected_lock_sha256:
        raise ValueError("image Harness Runtime lock differs from the active common runtime")
    model_checks = model.get("checks") if isinstance(model.get("checks"), dict) else {}
    harness_checks = harness.get("checks") if isinstance(harness.get("checks"), dict) else {}
    required_model = ("import", "load", "infer", "contract", "bounded_fixture_inference")
    required_harness = ("imports", "dataset_prepare", "server_orchestration", "prediction", "prediction_validation")
    failed_model = [name for name in required_model if not passed(model_checks.get(name))]
    failed_harness = [name for name in required_harness if not passed(harness_checks.get(name))]
    if failed_model:
        raise ValueError("docker_validation.json has failed Model Runtime checks: " + ", ".join(failed_model))
    if failed_harness:
        raise ValueError("docker_validation.json has failed Harness Runtime checks: " + ", ".join(failed_harness))
    return model, harness


def validate_container_documents(
    build: dict[str, Any],
    validation: dict[str, Any],
    registry: dict[str, Any],
) -> tuple[str, str, str]:
    for name, payload in (("docker_build_result.json", build), ("docker_validation.json", validation), ("docker_registry_result.json", registry)):
        if not passed(payload.get("status")):
            raise ValueError(f"{name}.status must be passed")
    image, digest, image_ref = validate_image_and_digest(
        registry.get("target_image"), registry.get("target_image_digest")
    )
    for name, payload in (("build", build), ("validation", validation)):
        if payload.get("target_image") != image or payload.get("target_image_digest") != digest:
            raise ValueError(f"{name} image binding disagrees with docker_registry_result.json")
    if build.get("target_image_ref") != image_ref or validation.get("target_image_ref") != image_ref:
        raise ValueError("container artifacts must use the same digest-pinned target_image_ref")
    base_image = build.get("base_image")
    if not isinstance(base_image, str) or not base_image:
        raise ValueError("docker_build_result.json must distinguish a non-empty base_image")
    if base_image == image:
        raise ValueError("base_image and target_image must not be identical")
    checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
    required_checks = ("import", "load", "infer", "contract", "bounded_fixture_inference")
    failed = [name for name in required_checks if not passed(checks.get(name))]
    if failed:
        raise ValueError("docker_validation.json has failed checks: " + ", ".join(failed))
    validate_runtime_roles(validation)
    push = registry.get("push") if isinstance(registry.get("push"), dict) else {}
    pull = registry.get("pull_verify") if isinstance(registry.get("pull_verify"), dict) else {}
    if not passed(push.get("status")) or not passed(pull.get("status")):
        raise ValueError("registry push and digest pull verification must both pass")
    if pull.get("digest") != digest:
        raise ValueError("pull_verify.digest must equal target_image_digest")
    return image, digest, image_ref
