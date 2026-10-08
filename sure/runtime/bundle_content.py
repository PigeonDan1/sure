"""The bundle-content contract shared between producers and consumers.

"What counts as deployable bundle content" is a contract, not a local
detail: the onboard producer must not declare excluded paths as required
artifacts, and the approve consumer excludes them from candidate copies and
digests. Each side keeping its own exclusion list is what let cache
bookkeeping become a required deployment artifact that the candidate was
forbidden to carry.
"""
from __future__ import annotations

import os
from pathlib import Path

EXCLUDED_DIR_NAMES = frozenset({".cache", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"})
EXCLUDED_TOP_LEVEL = frozenset({"eval_runs", "evaluation_runs", "results"})


def is_excluded(relative: Path) -> bool:
    """Whether a bundle-relative path falls outside the deployable content."""
    parts = relative.parts
    return bool(parts) and (
        parts[0] in EXCLUDED_TOP_LEVEL or any(part in EXCLUDED_DIR_NAMES for part in parts)
    )


def iter_bundle_files(root: Path, *, bundle_root: Path | None = None) -> list[Path]:
    """Files under root, applying exclusions relative to the whole bundle.

    Directory symlinks are not followed (os.walk default); file symlinks are
    yielded unresolved so callers can inspect them. The result is sorted by
    bundle-relative path for deterministic downstream digests.
    """
    bundle_root = bundle_root if bundle_root is not None else root
    if is_excluded(root.relative_to(bundle_root)):
        return []
    if root.is_file():
        return [root]
    files: list[Path] = []
    for dirpath, dir_names, file_names in os.walk(root):
        directory = Path(dirpath)
        dir_names[:] = sorted(
            name for name in dir_names
            if not is_excluded((directory / name).relative_to(bundle_root))
        )
        for name in file_names:
            path = directory / name
            if not is_excluded(path.relative_to(bundle_root)) and path.is_file():
                files.append(path)
    return sorted(files, key=lambda path: path.relative_to(bundle_root).as_posix())
