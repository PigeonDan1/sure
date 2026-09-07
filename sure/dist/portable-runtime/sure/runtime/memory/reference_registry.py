"""Host-neutral registry for SURE memory reference aliases.

The memory state store (``sure/memory``) and the human-reviewed reference
documents are different concerns.  This module owns only the latter's logical
identity and filesystem aliases.  It deliberately has no dependency on the
Pi hook layer, memory promotion rules, or a particular executor.

During the compatibility migration the legacy ``sure/skills`` tree remains
the write target and the first read candidate.  A canonical tree can be
admitted explicitly by a caller without changing entry ids, URI identity, or
the index schema.  Keeping this decision in one registry prevents each
writer/indexer from growing a second copy of the path convention.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

ENTRY_KINDS = ("bad_case", "fact")
URI_PREFIX = "memory://"
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def _safe_segment(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SEGMENT_RE.fullmatch(value) or value in (".", ".."):
        raise ValueError(f"{label} must be one safe path segment: {value!r}")
    return value


def _split_entry_id(entry_id: str) -> tuple[str, str]:
    if not isinstance(entry_id, str) or entry_id.count("/") != 1:
        raise ValueError(f"memory entry id must be <skill>/<slug>: {entry_id!r}")
    skill, slug = entry_id.split("/", 1)
    return _safe_segment(skill, "memory skill"), _safe_segment(slug, "memory slug")


def _kind(value: str) -> str:
    if value not in ENTRY_KINDS:
        raise ValueError(f"memory reference kind must be one of {ENTRY_KINDS}: {value!r}")
    return value


def _validate_kind_for_skill(skill: str, entry_type: str) -> None:
    if entry_type == "fact" and skill != "_shared":
        raise ValueError("memory facts must use the _shared skill")
    if entry_type == "bad_case" and skill == "_shared":
        raise ValueError("memory bad cases must use a skill-specific namespace")


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _admit(root: Path, candidate: Path, label: str) -> Path:
    """Resolve a reference path and reject lexical or symlink escapes.

    ``Path.resolve(strict=False)`` follows links that already exist while
    retaining a useful path for a not-yet-created leaf.  Checking the resolved
    root and candidate catches both an existing escape and a dangling link
    whose target is outside the admitted root.  A caller may intentionally
    choose roots outside the repository; containment is always relative to
    that explicit root, not to the process cwd.
    """
    root = Path(root).absolute()
    candidate = Path(candidate).absolute()
    real_root = Path(os.path.realpath(root))
    real_candidate = Path(os.path.realpath(candidate))
    if not _inside(root, candidate):
        raise ValueError(f"{label} escapes its admitted root: {candidate}")
    if not _inside(real_root, real_candidate):
        raise ValueError(f"{label} resolves outside its admitted root: {candidate}")
    return candidate


@dataclass(frozen=True)
class ReferenceRoots:
    repo_root: Path
    memory_root: Path
    canonical_root: Path
    legacy_skills_root: Path
    write_root: str = "legacy"
    read_order: tuple[str, ...] = ("legacy", "canonical")


class ReferenceRegistry:
    """Resolve logical memory references to canonical and compatibility aliases."""

    def __init__(
        self,
        repo_root: Path,
        *,
        memory_root: Path | None = None,
        canonical_root: Path | None = None,
        legacy_skills_root: Path | None = None,
        write_root: str = "legacy",
        read_order: Iterable[str] | None = None,
    ) -> None:
        repo = Path(repo_root).resolve()
        canonical = Path(canonical_root or repo / "sure" / "canonical").resolve()
        legacy = Path(legacy_skills_root or repo / "sure" / "skills").resolve()
        if write_root not in ("legacy", "canonical"):
            raise ValueError(f"reference write_root must be legacy or canonical: {write_root!r}")
        order = tuple(read_order or ("legacy", "canonical"))
        if not order or any(item not in ("legacy", "canonical") for item in order) or len(set(order)) != len(order):
            raise ValueError("reference read_order must list legacy and/or canonical exactly once")
        memory = Path(memory_root or repo / "sure" / "memory").resolve()
        self.roots = ReferenceRoots(repo, memory, canonical, legacy, write_root, order)

    @property
    def repo_root(self) -> Path:
        return self.roots.repo_root

    @property
    def memory_root(self) -> Path:
        return self.roots.memory_root

    def logical_uri(self, entry_id: str, entry_type: str | None = None) -> str:
        skill, slug = _split_entry_id(entry_id)
        resolved_type = "fact" if entry_type == "fact" or (entry_type is None and skill == "_shared") else "bad_case"
        _validate_kind_for_skill(skill, resolved_type)
        return f"{URI_PREFIX}{skill}/{_kind(resolved_type)}/{slug}"

    def parse_uri(self, uri: str) -> tuple[str, str, str]:
        if not isinstance(uri, str) or not uri.startswith(URI_PREFIX):
            raise ValueError(f"memory URI must start with {URI_PREFIX}: {uri!r}")
        parts = uri[len(URI_PREFIX) :].split("/")
        if len(parts) != 3:
            raise ValueError(f"memory URI must be <skill>/<kind>/<slug>: {uri!r}")
        skill = _safe_segment(parts[0], "memory skill")
        entry_type = _kind(parts[1])
        _validate_kind_for_skill(skill, entry_type)
        return skill, entry_type, _safe_segment(parts[2], "memory slug")

    def _path(self, skill: str, entry_type: str, slug: str, alias: str) -> Path:
        skill = _safe_segment(skill, "memory skill")
        slug = _safe_segment(slug, "memory slug")
        entry_type = _kind(entry_type)
        _validate_kind_for_skill(skill, entry_type)
        if alias == "legacy":
            root = self.roots.legacy_skills_root
            base = root / "_shared" / "memory" / "facts" if entry_type == "fact" else root / skill / "references" / "memory" / "bad_cases"
        elif alias == "canonical":
            root = self.roots.canonical_root
            canonical_skill = skill.replace("_", "-")
            base = root / "shared" / "legacy-resources" / "memory" / "facts" if entry_type == "fact" else root / "skills" / canonical_skill / "references" / "memory" / "bad_cases"
        else:
            raise ValueError(f"unknown reference alias: {alias!r}")
        return _admit(root, base / f"{slug}.md", f"{alias} memory reference")

    def path_for(self, entry_id: str, entry_type: str | None = None, *, alias: str | None = None) -> Path:
        skill, slug = _split_entry_id(entry_id)
        resolved_type = "fact" if entry_type == "fact" or (entry_type is None and skill == "_shared") else "bad_case"
        _validate_kind_for_skill(skill, resolved_type)
        selected = alias or self.roots.write_root
        return self._path(skill, resolved_type, slug, selected)

    def candidates(self, entry_id: str, entry_type: str | None = None) -> list[Path]:
        skill, slug = _split_entry_id(entry_id)
        resolved_type = "fact" if entry_type == "fact" or (entry_type is None and skill == "_shared") else "bad_case"
        _validate_kind_for_skill(skill, resolved_type)
        return [self._path(skill, resolved_type, slug, alias) for alias in self.roots.read_order]

    def resolve(self, entry_id: str, entry_type: str | None = None) -> Path | None:
        skill, slug = _split_entry_id(entry_id)
        resolved_type = "fact" if entry_type == "fact" or (entry_type is None and skill == "_shared") else "bad_case"
        _validate_kind_for_skill(skill, resolved_type)
        for alias in self.roots.read_order:
            try:
                path = self._path(skill, resolved_type, slug, alias)
            except ValueError:
                # A tampered alias is unavailable to readers. Callers that
                # need to distinguish policy violations can use candidates().
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            return path
        return None

    def directory(self, skill: str, entry_type: str, *, alias: str) -> Path:
        skill = _safe_segment(skill, "memory skill")
        entry_type = _kind(entry_type)
        _validate_kind_for_skill(skill, entry_type)
        if alias == "legacy":
            root = self.roots.legacy_skills_root
            directory = root / "_shared" / "memory" / "facts" if entry_type == "fact" else root / skill / "references" / "memory" / "bad_cases"
        elif alias == "canonical":
            root = self.roots.canonical_root
            directory = root / "shared" / "legacy-resources" / "memory" / "facts" if entry_type == "fact" else root / "skills" / skill.replace("_", "-") / "references" / "memory" / "bad_cases"
        else:
            raise ValueError(f"unknown reference alias: {alias!r}")
        return _admit(root, directory, f"{alias} memory reference directory")

    def iter_reference_dirs(self) -> Iterator[tuple[str, Path]]:
        """Yield ``(logical_skill, directory)`` in deterministic read order.

        Legacy directories are enumerated from the existing skills tree.  The
        canonical layout is enumerated from its shared facts directory and
        per-skill directories; absent roots are simply empty.  Duplicate
        physical paths are suppressed while both aliases remain visible when
        they are distinct.
        """
        seen: set[Path] = set()
        for alias in self.roots.read_order:
            root = self.roots.legacy_skills_root if alias == "legacy" else self.roots.canonical_root
            if not root.is_dir():
                continue
            if alias == "legacy":
                for skill_dir in sorted(path for path in root.iterdir() if path.is_dir()):
                    skill = skill_dir.name
                    kind = "fact" if skill == "_shared" else "bad_case"
                    try:
                        directory = self.directory(skill, kind, alias=alias)
                    except (OSError, ValueError):
                        continue
                    if directory.is_dir() and directory not in seen:
                        seen.add(directory)
                        yield skill, directory
            else:
                try:
                    facts = self.directory("_shared", "fact", alias=alias)
                except (OSError, ValueError):
                    facts = None
                if facts is not None and facts.is_dir() and facts not in seen:
                    seen.add(facts)
                    yield "_shared", facts
                skills = root / "skills"
                try:
                    if not skills.is_dir() or not _inside(root, skills.resolve()):
                        continue
                except OSError:
                    continue
                for skill_dir in sorted(path for path in skills.iterdir() if path.is_dir()):
                    try:
                        directory = self.directory(skill_dir.name.replace("-", "_"), "bad_case", alias=alias)
                    except (OSError, ValueError):
                        continue
                    if directory.is_dir() and directory not in seen:
                        seen.add(directory)
                        yield skill_dir.name.replace("-", "_"), directory

    def iter_reference_files(self) -> Iterator[tuple[str, Path]]:
        seen: set[str] = set()
        for skill, directory in self.iter_reference_dirs():
            for path in sorted(directory.glob("*.md")):
                if path.name.lower() == "readme.md":
                    continue
                try:
                    _admit(directory, path, "memory reference file")
                    if path.is_symlink() or not path.is_file():
                        continue
                except (OSError, ValueError):
                    continue
                entry_id = f"{skill}/{path.stem}"
                try:
                    _split_entry_id(entry_id)
                except ValueError:
                    continue
                if entry_id in seen:
                    continue
                seen.add(entry_id)
                yield entry_id, path


def registry_for(repo_root: Path, **kwargs: object) -> ReferenceRegistry:
    """Small factory used by legacy modules to make the dependency explicit."""
    return ReferenceRegistry(Path(repo_root), **kwargs)  # type: ignore[arg-type]
