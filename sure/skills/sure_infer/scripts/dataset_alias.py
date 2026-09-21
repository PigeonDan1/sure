#!/usr/bin/env python3
"""Shared dataset-alias resolution for /sure_infer and /sure_eval.

Short aliases such as ``aishell1`` resolve to the unique fully qualified
projection id on disk, e.g. ``aishell1__v1.0.2__asr`` (3-seg formal id after
multi-task prepare). Source-root pipelines use the same prefix rule for
``source__version`` and ``source__version__task``.

Used by DatasetManager JSONL lookup and any caller that maps a short name
onto a set of known stems. Do not re-implement the prefix rule elsewhere.
"""

from __future__ import annotations

from typing import Iterable


def resolve_dataset_alias(name: str, available_names: Iterable[str]) -> str | None:
    """Resolve a short dataset alias against a set of known dataset names.

    - An exact match in ``available_names`` is returned unchanged.
    - Otherwise, if exactly one name in ``available_names`` is a versioned
      projection of ``name`` (starts with ``"{name}__"``, e.g. ``aishell1``
      -> ``aishell1__v1.0.2__asr``), that unique match is returned. Source-root
      pipeline ids follow the same ``<source_dataset_name>__<version_id>``
      shape, e.g. ``demo_speech_zh_test__v1.0.2``; the prefix match
      applies the same way regardless of segment count.
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
