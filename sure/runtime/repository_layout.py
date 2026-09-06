#!/usr/bin/env python3
"""Resolve writable repository and read-only runtime roots without host coupling."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

REPOSITORY_ROOT_ENV = "SURE_REPOSITORY_ROOT"
RUNTIME_SUPPORT_ROOT_ENV = "SURE_RUNTIME_SUPPORT_ROOT"
LOCAL_RESULTS_ROOT_ENV = "SURE_LOCAL_RESULTS_ROOT"
EVALUATION_HOME_ENV = "SURE_EVALUATION_HOME"


class RepositoryLayoutError(RuntimeError):
    """A required SURE root is absent or points to the wrong kind of directory."""


def _configured_directory(environment: Mapping[str, str], name: str) -> Path | None:
    value = str(environment.get(name) or "").strip()
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise RepositoryLayoutError(f"{name} does not name an existing directory: {path}")
    return path


def _parents(script_file: str | Path) -> tuple[Path, ...]:
    script = Path(script_file).expanduser().resolve()
    return (script.parent, *script.parents)


def repository_root(
    script_file: str | Path,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the writable run workspace, never an installed runtime by accident."""

    env = os.environ if environment is None else environment
    configured = _configured_directory(env, REPOSITORY_ROOT_ENV)
    if configured is not None:
        return configured
    parents = _parents(script_file)
    for candidate in parents:
        runtime = candidate / "sure" / "runtime"
        source = candidate / "sure" / "skills"
        canonical = candidate / "sure" / "canonical" / "skills"
        if runtime.is_dir() and (source.is_dir() or canonical.is_dir()):
            return candidate
    if any((candidate / "runtime-support.lock.json").is_file() for candidate in parents):
        raise RepositoryLayoutError(
            f"{REPOSITORY_ROOT_ENV} is required when executing an installed SURE runtime"
        )
    script = Path(script_file).expanduser().resolve()
    raise RepositoryLayoutError(f"cannot discover {REPOSITORY_ROOT_ENV} from {script}")


def runtime_support_root(
    script_file: str | Path,
    *,
    repository: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the root that owns immutable runtime/site support files."""

    env = os.environ if environment is None else environment
    configured = _configured_directory(env, RUNTIME_SUPPORT_ROOT_ENV)
    if configured is not None:
        if (
            (configured / "sure" / "runtime" / "evaluation" / "runtime.json").is_file()
            and (configured / "sure" / "site" / "loader.py").is_file()
        ):
            return configured
        raise RepositoryLayoutError(f"{RUNTIME_SUPPORT_ROOT_ENV} is not a SURE runtime: {configured}")
    candidates = list(_parents(script_file))
    if repository is not None:
        candidates.append(repository.expanduser().resolve())
    for candidate in dict.fromkeys(candidates):
        if (
            (candidate / "sure" / "runtime" / "evaluation" / "runtime.json").is_file()
            and (candidate / "sure" / "site" / "loader.py").is_file()
        ):
            return candidate
    raise RepositoryLayoutError(f"cannot discover {RUNTIME_SUPPORT_ROOT_ENV}")


def local_results_root(repository: Path, environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    value = str(env.get(LOCAL_RESULTS_ROOT_ENV) or "").strip()
    return Path(value).expanduser().resolve() if value else (repository / "sure" / "results").resolve()


def evaluation_engine_root(repository: Path, environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    value = str(env.get(EVALUATION_HOME_ENV) or "").strip()
    return (
        Path(value).expanduser().resolve()
        if value
        else (repository / "sure" / "external" / "sure-evaluation").resolve()
    )
