#!/usr/bin/env python3
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_ALLOWED_TEMPLATE_FIELDS = {"registry", "task", "model_name"}
_REQUIRED_TEMPLATE_FIELDS = {"registry", "model_name"}
_TEMPLATE_FIELD_RE = re.compile(r"\{([^{}]+)\}")
_REPOSITORY_COMPONENT_RE = re.compile(r"^[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


class ContainerDeliveryError(ValueError):
    pass


def safe_image_component(value: str, *, location: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("._-").lower()
    if not component:
        raise ContainerDeliveryError(f"{location} does not contain a valid image component")
    return component


def validate_repository_template(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ContainerDeliveryError("repository_template must be a non-empty string")
    fields = set(_TEMPLATE_FIELD_RE.findall(value))
    remainder = _TEMPLATE_FIELD_RE.sub("", value)
    if "{" in remainder or "}" in remainder:
        raise ContainerDeliveryError("repository_template contains malformed braces")
    unknown = sorted(fields - _ALLOWED_TEMPLATE_FIELDS)
    if unknown:
        raise ContainerDeliveryError(f"repository_template has unsupported field: {unknown[0]}")
    missing = sorted(_REQUIRED_TEMPLATE_FIELDS - fields)
    if missing:
        raise ContainerDeliveryError(f"repository_template is missing field: {missing[0]}")
    if not value.startswith("{registry}/"):
        raise ContainerDeliveryError("repository_template must start with {registry}/")
    if any(character.isspace() for character in value) or "@" in value:
        raise ContainerDeliveryError("repository_template must not contain whitespace or a digest")
    return value


def resolve_container_repository(
    policy: Mapping[str, Any],
    *,
    task_type: str,
    model_name: str,
    stage: str | None = None,
) -> str:
    network = policy.get("network")
    delivery = policy.get("container_delivery")
    if not isinstance(network, Mapping) or not network.get("container_registry"):
        raise ContainerDeliveryError(
            "site policy is missing network.container_registry required by package=docker-registry"
        )
    if not isinstance(delivery, Mapping) or not delivery.get("repository_template"):
        raise ContainerDeliveryError(
            "site policy is missing container_delivery.repository_template required by package=docker-registry"
        )

    registry = str(network["container_registry"]).strip().rstrip("/")
    if not registry or "://" in registry or any(character.isspace() for character in registry):
        raise ContainerDeliveryError("network.container_registry must be a registry host or host/path prefix")
    template = validate_repository_template(delivery["repository_template"])
    repository = template.format(
        registry=registry,
        task=safe_image_component(task_type, location="task_type"),
        model_name=safe_image_component(model_name, location="model_name"),
    )
    if stage is not None:
        repository += f"-{safe_image_component(stage, location='stage')}"
    _validate_repository(repository, registry=registry)
    return repository


def resolve_container_image(
    policy: Mapping[str, Any],
    *,
    task_type: str,
    model_name: str,
    version: str,
    stage: str | None = None,
) -> str:
    if _TAG_RE.fullmatch(version or "") is None:
        raise ContainerDeliveryError(f"invalid container image version: {version!r}")
    repository = resolve_container_repository(
        policy,
        task_type=task_type,
        model_name=model_name,
        stage=stage,
    )
    return f"{repository}:{version}"


def _validate_repository(repository: str, *, registry: str) -> None:
    if not repository.startswith(f"{registry}/"):
        raise ContainerDeliveryError("resolved repository is outside network.container_registry")
    if "//" in repository or "@" in repository or any(character.isspace() for character in repository):
        raise ContainerDeliveryError("resolved repository is not a valid Docker repository")
    path = repository[len(registry) + 1 :]
    components = path.split("/")
    if not components or any(_REPOSITORY_COMPONENT_RE.fullmatch(component) is None for component in components):
        raise ContainerDeliveryError("resolved repository contains an invalid path component")
