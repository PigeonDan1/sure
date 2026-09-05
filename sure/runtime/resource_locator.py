"""Host-neutral, read-only lookup for SURE skill and validator resources.

The legacy tree is still the default during migration.  A runner may pin a
canonical or backend root explicitly through the environment, which lets Pi,
portable skills, and future executors resolve the same resource identity
without importing a sibling skill by walking ``Path(__file__).parents``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

REPOSITORY_ROOT_ENV = "SURE_REPOSITORY_ROOT"
CANONICAL_SKILLS_ROOT_ENV = "SURE_CANONICAL_SKILLS_ROOT"
LEGACY_SKILLS_ROOT_ENV = "SURE_LEGACY_SKILLS_ROOT"
SEMANTIC_BACKEND_ROOT_ENV = "SURE_SEMANTIC_BACKEND_ROOT"


class ResourceResolutionError(FileNotFoundError):
    """A requested SURE resource is absent or outside an admitted root."""


def _skill_slug(skill_id: str) -> str:
    value = str(skill_id or "").strip()
    if not value or "_" not in value or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in value):
        raise ResourceResolutionError(f"invalid SURE skill id: {skill_id!r}")
    return value.replace("_", "-")


def _relative_resource(resource: str) -> PurePosixPath:
    value = str(resource or "").strip().replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ResourceResolutionError(f"resource path must be relative and non-escaping: {resource!r}")
    return path


def _repository_root(env: Mapping[str, str]) -> Path:
    explicit = str(env.get(REPOSITORY_ROOT_ENV) or "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_dir():
            raise ResourceResolutionError(f"repository root does not exist: {path}")
        return path
    for parent in Path(__file__).resolve().parents:
        if (parent / "sure" / "skills").is_dir() and (parent / "sure" / "canonical").is_dir():
            return parent
    raise ResourceResolutionError("cannot discover the SURE repository root")


@dataclass(frozen=True)
class ResourceLocator:
    """Resolve resources without writing or following a path outside its root."""

    repository_root: Path
    canonical_skills_root: Path | None = None
    legacy_skills_root: Path | None = None
    semantic_backend_root: Path | None = None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "ResourceLocator":
        env = os.environ if environment is None else environment
        root = _repository_root(env)

        def optional_path(name: str) -> Path | None:
            raw = str(env.get(name) or "").strip()
            return Path(raw).expanduser().resolve() if raw else None

        return cls(
            repository_root=root,
            canonical_skills_root=optional_path(CANONICAL_SKILLS_ROOT_ENV),
            legacy_skills_root=optional_path(LEGACY_SKILLS_ROOT_ENV),
            semantic_backend_root=optional_path(SEMANTIC_BACKEND_ROOT_ENV),
        )

    def _candidate_roots(self, skill_id: str, *, backend: bool) -> tuple[Path, ...]:
        slug = _skill_slug(skill_id)
        candidates: list[Path] = []
        if backend and self.semantic_backend_root is not None:
            candidates.extend((self.semantic_backend_root / slug, self.semantic_backend_root / skill_id))
        if self.canonical_skills_root is not None:
            candidates.append(self.canonical_skills_root / slug)
        if self.legacy_skills_root is not None:
            candidates.append(self.legacy_skills_root / skill_id)
        # Legacy is deliberately first by default: the compatibility branch
        # must remain byte/exit compatible until a backend opts in explicitly.
        if backend and self.semantic_backend_root is None:
            candidates.append(self.repository_root / "sure" / "skills" / skill_id)
        candidates.append(self.repository_root / "sure" / "canonical" / "skills" / slug)
        candidates.append(self.repository_root / "sure" / "skills" / skill_id)
        return tuple(dict.fromkeys(path.resolve() for path in candidates))

    def resolve_skill_resource(self, skill_id: str, resource: str, *, backend: bool = False) -> Path:
        relative = _relative_resource(resource)
        candidates = tuple(root / relative for root in self._candidate_roots(skill_id, backend=backend))
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        formatted = ", ".join(str(candidate) for candidate in candidates)
        raise ResourceResolutionError(f"SURE resource is not available: {skill_id}/{relative}; checked: {formatted}")

    def resolve_script(self, skill_id: str, script: str, *, backend: bool = True) -> Path:
        relative = _relative_resource(script)
        if relative.parts and relative.parts[0] != "scripts":
            relative = PurePosixPath("scripts") / relative
        return self.resolve_skill_resource(skill_id, relative.as_posix(), backend=backend)


def resolve_skill_resource(
    skill_id: str,
    resource: str,
    *,
    environment: Mapping[str, str] | None = None,
    backend: bool = False,
) -> Path:
    return ResourceLocator.from_environment(environment).resolve_skill_resource(skill_id, resource, backend=backend)


def resolve_skill_script(
    skill_id: str,
    script: str,
    *,
    environment: Mapping[str, str] | None = None,
    backend: bool = True,
) -> Path:
    return ResourceLocator.from_environment(environment).resolve_script(skill_id, script, backend=backend)
