#!/usr/bin/env python3
"""Small site-policy dependency used by the side-effect-free TRANS validator."""
from __future__ import annotations

import sys
from pathlib import Path


for _parent in Path(__file__).resolve().parents:
    if (_parent / "sure" / "site" / "loader.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from sure.site.loader import load_site_policy


def default_partition() -> str:
    """Return the site policy's dedicated VC partition for GPU evidence."""
    resolved = load_site_policy(required=True) or {}
    value = resolved.get("policy", {}).get("execution", {}).get("vc_default_partition")
    if not value:
        raise ValueError(
            "site policy is missing execution.vc_default_partition; set it in "
            "config/site.bundled.yaml or config/site.local.yaml"
        )
    return str(value)
