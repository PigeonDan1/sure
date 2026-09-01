#!/usr/bin/env python3
"""Tests: source entries flow through normalize/download_and_convert/prepare.

Run directly:
    cd sure/skills/sure_eval/scripts && python test_source_naming_flow.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_sure_dataset  # noqa: E402
from sure_eval.datasets import source_resolver  # noqa: E402
from test_source_conversion import make_manager, make_source_tree  # noqa: E402


class SourceNamingFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source_root = self.tmp / "src"
        self._env = mock.patch.dict(
            os.environ, {source_resolver.SOURCE_ROOT_ENV: str(self.source_root)}
        )
        self._env.start()
        self.dataset_root = make_source_tree(self.source_root, "demo_ds", "v1.0.2")
        self.manager = make_manager(self.tmp)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_normalize_resolves_source_entry_to_dataset_id(self) -> None:
        self.assertEqual(
            self.manager.normalize_dataset_name(str(self.dataset_root)), "demo_ds__v1.0.2"
        )

    def test_normalize_keeps_returning_plain_names_unchanged(self) -> None:
        self.assertEqual(self.manager.normalize_dataset_name("no_such_name"), "no_such_name")

    def test_download_and_convert_accepts_source_entry(self) -> None:
        jsonl_path = self.manager.download_and_convert(str(self.dataset_root))
        self.assertEqual(jsonl_path.name, "demo_ds__v1.0.2.jsonl")
        self.assertTrue(jsonl_path.exists())

    def test_get_info_surfaces_source_fields(self) -> None:
        self.manager.download_and_convert(str(self.dataset_root))
        info = self.manager.get_info("demo_ds__v1.0.2") or {}
        self.assertEqual(info["source"], "site_dataset_pool")
        self.assertEqual(info["source_dataset_name"], "demo_ds")
        self.assertEqual(info["version_id"], "v1.0.2")
        self.assertEqual(info["source_root"], str(self.dataset_root))

    def test_prepare_dataset_emits_plan_fields(self) -> None:
        summary = prepare_sure_dataset.prepare_dataset(
            self.manager, "demo_ds__v1.0.2", requested_name=str(self.dataset_root)
        )
        self.assertEqual(summary["dataset"], "demo_ds__v1.0.2")
        self.assertEqual(summary["requested_name"], str(self.dataset_root))
        self.assertEqual(summary["source_dataset_root"], str(self.dataset_root))
        self.assertEqual(summary["source_dataset_name"], "demo_ds")
        self.assertEqual(summary["version_id"], "v1.0.2")
        self.assertEqual(summary["task"], "ASR")

    def test_expand_dataset_names_yields_dataset_id_for_source_entry(self) -> None:
        expanded = self.manager.expand_dataset_names([str(self.dataset_root)])
        self.assertEqual(expanded, ["demo_ds__v1.0.2"])

    def test_at_suffix_flows_through_normalize_and_convert(self) -> None:
        multi_root = make_source_tree(self.source_root, "multi_ds", "v1.0.1")
        make_source_tree(self.source_root, "multi_ds", "v2.0.0")
        entry = f"{multi_root}@v2.0.0"
        self.assertEqual(self.manager.normalize_dataset_name(entry), "multi_ds__v2.0.0")
        jsonl_path = self.manager.download_and_convert(entry)
        self.assertEqual(jsonl_path.name, "multi_ds__v2.0.0.jsonl")


if __name__ == "__main__":
    unittest.main()
