#!/usr/bin/env python3
"""Resolve the host Docker CLI used by SURE-EVAL launchers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable


DOCKER_BIN_ENV = "SURE_EVAL_DOCKER_BIN"
SHARED_DOCKER_BIN_ENV = "SURE_DOCKER_BIN"
SYSTEM_DOCKER_CANDIDATES = (
    Path("/usr/bin/docker"),
    Path("/usr/local/bin/docker"),
    Path("/bin/docker"),
)


def _executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_docker_binary(*, which: Callable[[str], str | None] = shutil.which) -> str:
    for env_name in (DOCKER_BIN_ENV, SHARED_DOCKER_BIN_ENV):
        override = os.environ.get(env_name, "").strip()
        if not override:
            continue
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{env_name} must be an absolute path")
        if not _executable_file(path):
            raise ValueError(f"{env_name} is not executable: {path}")
        return str(path)

    for candidate in SYSTEM_DOCKER_CANDIDATES:
        if _executable_file(candidate):
            return str(candidate)

    return which("docker") or "docker"
