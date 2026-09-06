"""Activate the immutable SURE runtime support package for this backend."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

RUNTIME_SUPPORT_ROOT_ENV = "SURE_RUNTIME_SUPPORT_ROOT"


def activate_runtime_support(
    script_file: str | Path,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Put the runtime support root on ``sys.path`` and return it.

    An explicit root is authoritative. A bad explicit value must not silently
    fall back to whichever repository happens to contain the entrypoint.
    """

    env = os.environ if environment is None else environment
    configured = str(env.get(RUNTIME_SUPPORT_ROOT_ENV) or "").strip()
    script = Path(script_file).expanduser().resolve()
    if configured:
        candidates = (Path(configured).expanduser().resolve(),)
    else:
        candidates = (script.parent, *script.parents)
    for candidate in candidates:
        if (candidate / "sure" / "runtime" / "repository_layout.py").is_file():
            value = str(candidate)
            if value not in sys.path:
                sys.path.insert(0, value)
            return candidate
    if configured:
        raise RuntimeError(f"{RUNTIME_SUPPORT_ROOT_ENV} is not a SURE runtime: {configured}")
    raise RuntimeError(f"cannot discover {RUNTIME_SUPPORT_ROOT_ENV} from {script}")
