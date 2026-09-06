#!/usr/bin/env python3
"""Host-neutral entrypoint for the existing SURE memory extraction gate."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _runtime_dir() -> Path | None:
    configured = os.environ.get("SURE_RUNTIME_SUPPORT_ROOT", "").strip()
    if not configured:
        print("check_memory_extraction backend requires SURE_RUNTIME_SUPPORT_ROOT", file=sys.stderr)
        return None
    runtime_dir = Path(configured).expanduser().resolve() / "sure" / "runtime"
    entrypoint = runtime_dir / "memory" / "proposals.py"
    try:
        entrypoint.resolve().relative_to(runtime_dir.resolve())
        if entrypoint.is_symlink() or not entrypoint.is_file():
            raise OSError
    except (OSError, ValueError):
        print("check_memory_extraction backend runtime is unavailable", file=sys.stderr)
        return None
    return runtime_dir


def main(argv: list[str]) -> int:
    runtime_dir = _runtime_dir()
    if runtime_dir is None:
        return 2
    sys.path.insert(0, str(runtime_dir))
    try:
        from memory import proposals
    except (ImportError, OSError):
        print("check_memory_extraction backend runtime is unavailable", file=sys.stderr)
        return 2
    arguments = list(argv)
    if "--repo-root" not in arguments:
        repository_root = os.environ.get("SURE_REPOSITORY_ROOT", "").strip()
        if not repository_root:
            print("check_memory_extraction backend requires SURE_REPOSITORY_ROOT", file=sys.stderr)
            return 2
        arguments.extend(["--repo-root", repository_root])
    return proposals.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
