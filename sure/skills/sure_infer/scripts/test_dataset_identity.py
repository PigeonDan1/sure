#!/usr/bin/env python3
"""Tests: canonical dataset id -> product-tree file stem mapping.

Run directly:
    cd sure/skills/sure_infer/scripts && python test_dataset_identity.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dataset_identity import projection_stems


class ProjectionStemsTests(unittest.TestCase):
    def test_a_suffixed_source_entry_maps_id_to_its_projection_stem(self) -> None:
        # The spelling follows the host that wrote eval_input_resolved.json, so
        # the sample uses the current platform's flavour.
        jsonl_dir = (
            PureWindowsPath(r"C:\proj\sure_benchmark\jsonl")
            if sys.platform == "win32"
            else PurePosixPath("/proj/sure_benchmark/jsonl")
        )
        entries = [
            {
                "name": "demo_ds__v1.0.2",
                "jsonl_path": str(jsonl_dir / "demo_ds__v1.0.2__sd.jsonl"),
            }
        ]
        self.assertEqual(projection_stems(entries), {"demo_ds__v1.0.2": "demo_ds__v1.0.2__sd"})

    def test_a_legacy_entry_without_task_suffix_maps_to_itself(self) -> None:
        # Forward slashes parse on every platform, so this spelling is host-neutral.
        entries = [
            {
                "name": "aishell1__v1.0.2",
                "jsonl_path": "/proj/sure_benchmark/jsonl/aishell1__v1.0.2.jsonl",
            }
        ]
        self.assertEqual(projection_stems(entries), {"aishell1__v1.0.2": "aishell1__v1.0.2"})

    def test_an_entry_without_jsonl_path_falls_back_to_the_name(self) -> None:
        self.assertEqual(projection_stems([{"name": "demo_ds"}]), {"demo_ds": "demo_ds"})

    def test_malformed_entries_are_skipped(self) -> None:
        self.assertEqual(
            projection_stems([None, "demo_ds", {"jsonl_path": "/x/y.jsonl"}, {"name": "kept", "jsonl_path": "/x/k.jsonl"}]),
            {"kept": "k"},
        )

    def test_a_non_list_input_yields_no_mappings(self) -> None:
        self.assertEqual(projection_stems(None), {})
        self.assertEqual(projection_stems({"name": "demo_ds"}), {})


if __name__ == "__main__":
    unittest.main()
