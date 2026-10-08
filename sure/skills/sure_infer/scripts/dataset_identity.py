#!/usr/bin/env python3
"""Canonical dataset id -> product-tree file stem, read from the resolved input.

Source-root datasets are written under their task-suffixed projection stem
(`<name>__<version>__<task>`; see DatasetManager.source_projection_name) while
the canonical id (`<name>__<version>`) is what eval_input_resolved.json,
execution_result.json and the run report use for reporting. The stem of a run
is already authoritative per dataset in
eval_input_resolved.json -> datasets[].jsonl_path, so every reader of the
product tree derives its file names from there instead of re-deriving them
from the id (or worse, guessing them from what happens to be on disk).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def projection_stems(entries: Any) -> dict[str, str]:
    """Map canonical dataset name -> product-tree file stem for resolved entries.

    Legacy projections have no task suffix, so their stem equals the id; the
    mapping is therefore safe to apply unconditionally.
    """
    if not isinstance(entries, list):
        return {}
    stems: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "")
        if not name:
            continue
        jsonl_path = str(entry.get("jsonl_path") or "")
        stems[name] = Path(jsonl_path).stem if jsonl_path else name
    return stems
