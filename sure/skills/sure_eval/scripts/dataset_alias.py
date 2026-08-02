#!/usr/bin/env python3
"""Shared dataset-alias resolution for /sure_eval and /sure_reval.

Both skills need to accept a short dataset alias such as ``aishell1`` and
resolve it to the fully qualified, versioned dataset id that /sure_eval
actually writes artifacts under, e.g. ``aishell1__v1.0.2__asr``.

/sure_eval already implements this rule for its own dataset directory in
``sure_eval.datasets.dataset_manager.DatasetManager._existing_jsonl_for_dataset``.
/sure_reval needs the identical rule applied to a different directory (its
own already-generated predictions, not the source dataset JSONL files), so
the rule is extracted here as a single, filesystem-agnostic implementation
that both call sites use. This is the one implementation of the rule; do not
re-implement it elsewhere.
"""

from __future__ import annotations

from typing import Iterable


def resolve_dataset_alias(name: str, available_names: Iterable[str]) -> str | None:
    """Resolve a short dataset alias against a set of known dataset names.

    - An exact match in ``available_names`` is returned unchanged.
    - Otherwise, if exactly one name in ``available_names`` is a versioned
      projection of ``name`` (starts with ``"{name}__"``, e.g. ``aishell1``
      -> ``aishell1__v1.0.2__asr``), that unique match is returned.
    - If there is no match, or more than one (an ambiguous short name),
      this returns ``None`` so the caller keeps its own "not found"
      handling instead of guessing a winner.
    """
    available = list(available_names)
    if name in available:
        return name
    prefix = f"{name}__"
    matches = sorted(item for item in available if item.startswith(prefix))
    return matches[0] if len(matches) == 1 else None
